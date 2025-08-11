import subprocess
import os
import tempfile
import time
import json
import shutil

class RunCommandNode:
    """
    🚨 EXTREME SECURITY RISK 🚨
    This node executes arbitrary shell commands on the server running ComfyUI.
    Use with extreme caution and only in completely isolated, trusted environments.
    Commands starting with '#' will be ignored.
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "command": ("STRING", {
                    "multiline": True,
                    "default": "# This is a comment and will be ignored\necho 'Hello from ComfyUI!'"
                }),
                "working_dir": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Optional: Working directory"
                }),
                "stop_on_error": ("BOOLEAN", {
                    "default": False
                }),
                "timeout": ("INT", {
                    "default": 30,
                    "min": 1,
                    "max": 600,
                    "step": 1,
                    "label": "Timeout per command (seconds)"
                }),
                "truncate_output": ("INT", {
                    "default": 2048,
                    "min": 256,
                    "max": 16384,
                    "step": 256,
                    "label": "Max output chars per command"
                }),
                "structured_output": ("BOOLEAN", {
                    "default": False
                }),
                "chain_commands": ("BOOLEAN", {
                    "default": False,
                    "label": "Chain commands (run as one shell script)"
                }),
                # New QoL inputs
                "shell": ("STRING", {
                    "default": "auto",
                    "choices": ["auto", "bash", "cmd", "powershell"],
                    "label": "Shell"
                }),
                "dry_run": ("BOOLEAN", {
                    "default": False
                }),
                "merge_stderr": ("BOOLEAN", {
                    "default": False,
                    "label": "Merge STDERR into STDOUT"
                }),
                "env_vars": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "KEY=VALUE (one per line)"
                }),
                "prepend_path": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "Paths to prepend to PATH (one per line)"
                }),
                "redact_patterns": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "Secrets to redact in output (one per line)"
                }),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("output",)
    FUNCTION = "execute_command"
    CATEGORY = "⚠️Utils/Execution (DANGEROUS)"

    def execute_command(self, command, working_dir, stop_on_error, timeout, truncate_output, structured_output, chain_commands,
                        shell, env_vars, prepend_path, dry_run, merge_stderr, redact_patterns):
        output_str = ""
        structured_results = []
        if not command or not command.strip():
            message = "Command is empty, ignoring."
            print(message)
            return (message,)

        # Expand working_dir (~ and env vars) and validate
        if working_dir:
            working_dir = os.path.expandvars(os.path.expanduser(working_dir))
        if working_dir and not os.path.isdir(working_dir):
            message = f"Working directory does not exist: {working_dir}"
            print(message)
            return (message,)

        # Shell selection
        is_windows = os.name == 'nt'
        shell = (shell or "auto").lower()
        chosen_shell = ("cmd" if is_windows else "bash") if shell == "auto" else shell

        # Prepare environment
        env = os.environ.copy()
        # Prepend PATH
        if prepend_path and prepend_path.strip():
            parts = [os.path.expandvars(os.path.expanduser(p.strip())) for p in prepend_path.splitlines() if p.strip()]
            if parts:
                env["PATH"] = os.pathsep.join(parts + [env.get("PATH", "")])
        # Custom ENV vars
        if env_vars and env_vars.strip():
            for ln in env_vars.splitlines():
                if not ln.strip() or ln.strip().startswith('#'):
                    continue
                if '=' not in ln:
                    continue
                k, v = ln.split('=', 1)
                k = k.strip()
                v = os.path.expandvars(v.strip())
                if k:
                    env[k] = v

        # Redaction list
        redactions = [s for s in (redact_patterns.splitlines() if redact_patterns else []) if s.strip()]

        def redact(s: str) -> str:
            if not s:
                return s
            for pat in redactions:
                s = s.replace(pat, "***")
            return s

        def clip(s: str) -> str:
            if s is None:
                return ""
            if len(s) > truncate_output:
                return s[:truncate_output] + "\n... (truncated)\n"
            return s

        lines = command.splitlines()
        summary = []
        executed_any = False

        # Helper: PowerShell executable resolution (supports Windows PowerShell and pwsh on non-Windows)
        def resolve_powershell():
            if is_windows:
                return "powershell.exe"
            return shutil.which("pwsh") or shutil.which("powershell")

        # --- Chain mode: write temp script and run once ---
        if chain_commands:
            commands_to_run = [line for line in lines if line.strip() and not line.strip().startswith('#')]
            if not commands_to_run:
                output_str += "No commands executed (all lines empty or commented).\n"
                return (output_str,)

            # Dry-run preview
            if dry_run:
                output_str += "DRY RUN (chain):\n" + "\n".join(commands_to_run) + "\n"
                structured_results.append({
                    "script": "(dry-run)",
                    "stdout": "",
                    "stderr": "",
                    "exit_code": -998,
                    "duration_s": 0.0
                })
                output_str += "\n--- SUMMARY ---\nShell Script Exit Code: -998 (dry-run)\n"
                return (json.dumps(structured_results, indent=2),) if structured_output else (output_str,)

            runner = None
            script_suffix = None
            header = ""
            if chosen_shell == "bash":
                if is_windows:
                    msg = "Bash shell not available on Windows for chained execution. Choose cmd or powershell."
                    print(msg)
                    return (msg,)
                runner = ["/bin/bash"]
                script_suffix = ".sh"
            elif chosen_shell == "cmd":
                if not is_windows:
                    msg = "cmd.exe is not available on this platform."
                    print(msg)
                    return (msg,)
                runner = ["cmd.exe", "/d", "/c"]
                script_suffix = ".bat"
                header = "@echo off\n"
            elif chosen_shell == "powershell":
                ps = resolve_powershell()
                if not ps:
                    msg = "PowerShell not found. Install PowerShell (pwsh) or use another shell."
                    print(msg)
                    return (msg,)
                runner = [ps, "-NoProfile"]
                if is_windows:
                    runner += ["-ExecutionPolicy", "Bypass"]
                runner += ["-File"]
                script_suffix = ".ps1"
                header = "$ErrorActionPreference = 'Stop'\n"
            else:
                msg = f"Unsupported shell: {chosen_shell}"
                print(msg)
                return (msg,)

            script_path = None
            with tempfile.NamedTemporaryFile('w', delete=False, suffix=script_suffix, encoding='utf-8') as script_file:
                script_path = script_file.name
                if header:
                    script_file.write(header)
                script_file.write('\n'.join(commands_to_run) + '\n')

            try:
                if not is_windows and chosen_shell == "bash":
                    os.chmod(script_path, 0o700)
                output_str += f"Executing as script: {script_path}\n"
                print(f"Executing script: {script_path}")
                start = time.time()
                result = subprocess.run(
                    runner + [script_path],
                    shell=False,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=(subprocess.STDOUT if merge_stderr else subprocess.PIPE),
                    text=True,
                    cwd=working_dir if working_dir else None,
                    env=env,
                    timeout=timeout
                )
                duration = round(time.time() - start, 4)
                std_out = result.stdout or ""
                std_err = "" if merge_stderr else (result.stderr or "")
                std_out = clip(redact(std_out))
                std_err = clip(redact(std_err))
                exit_code = result.returncode
                output_str += f"--- STDOUT ---\n{std_out}"
                if not merge_stderr:
                    output_str += f"--- STDERR ---\n{std_err}"
                output_str += f"--- Exit Code: {exit_code} ---\n"
                output_str += f"--- Duration: {duration}s ---\n"
                structured_results.append({
                    "script": script_path,
                    "stdout": std_out,
                    "stderr": std_err,
                    "exit_code": exit_code,
                    "duration_s": duration
                })
                summary.append(f"Shell Script Exit Code: {exit_code}")
            except subprocess.TimeoutExpired:
                msg = f"--- TIMEOUT ---\nShell script timed out after {timeout} seconds.\n"
                print(msg)
                output_str += msg
                structured_results.append({
                    "script": script_path or "",
                    "stdout": "",
                    "stderr": msg,
                    "exit_code": -999,
                    "duration_s": float(timeout)
                })
                summary.append("Shell Script Timeout")
            except Exception as e:
                error_message = f"--- EXECUTION ERROR ---\nFailed to execute shell script: {e}\n"
                print(error_message)
                output_str += error_message
                structured_results.append({
                    "script": script_path or "",
                    "stdout": "",
                    "stderr": error_message,
                    "exit_code": -1,
                    "duration_s": 0.0
                })
                summary.append("Shell Script Exception")
            finally:
                try:
                    if script_path:
                        os.remove(script_path)
                except Exception:
                    pass
            output_str += "\n--- SUMMARY ---\n" + "\n".join(summary) + "\n"
            return (json.dumps(structured_results, indent=2),) if structured_output else (output_str,)

        # --- Individual line mode (default) ---
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                output_str += f"[Line {idx}] Skipped: {line}\n"
                continue

            executed_any = True
            output_str += f"\n[Line {idx}] Executing: {stripped}\n"
            print(f"Executing line {idx}: {stripped}")

            # Dry-run per line
            if dry_run:
                structured_results.append({
                    "line": idx,
                    "command": stripped,
                    "stdout": "",
                    "stderr": "",
                    "exit_code": -998,
                    "duration_s": 0.0
                })
                output_str += "--- DRY RUN: Not executed ---\n"
                summary.append(f"[Line {idx}] Exit Code: -998 (dry-run)")
                continue

            try:
                run_kwargs = {
                    "check": False,
                    "stdout": subprocess.PIPE,
                    "stderr": (subprocess.STDOUT if merge_stderr else subprocess.PIPE),
                    "text": True,
                    "cwd": working_dir if working_dir else None,
                    "env": env,
                    "timeout": timeout
                }

                args = None
                use_shell = False

                if chosen_shell == "bash":
                    if is_windows:
                        raise RuntimeError("Bash is not available on Windows for per-line execution. Choose cmd or powershell.")
                    use_shell = True
                    run_kwargs["executable"] = "/bin/bash"
                    args = stripped
                elif chosen_shell == "cmd":
                    if not is_windows:
                        raise RuntimeError("cmd.exe is not available on this platform.")
                    use_shell = True
                    args = stripped
                elif chosen_shell == "powershell":
                    ps = resolve_powershell()
                    if not ps:
                        raise RuntimeError("PowerShell not found. Install PowerShell (pwsh) or use another shell.")
                    args = [ps, "-NoProfile"]
                    if is_windows:
                        args += ["-ExecutionPolicy", "Bypass"]
                    args += ["-Command", stripped]
                else:
                    raise RuntimeError(f"Unsupported shell: {chosen_shell}")

                start = time.time()
                if use_shell:
                    run_kwargs["shell"] = True
                    result = subprocess.run(args, **run_kwargs)
                else:
                    run_kwargs["shell"] = False
                    result = subprocess.run(args, **run_kwargs)

                duration = round(time.time() - start, 4)
                std_out = result.stdout or ""
                std_err = "" if merge_stderr else (result.stderr or "")
                std_out = clip(redact(std_out))
                std_err = clip(redact(std_err))
                exit_code = result.returncode

                output_str += f"--- STDOUT ---\n{std_out}"
                if not merge_stderr:
                    output_str += f"--- STDERR ---\n{std_err}"
                output_str += f"--- Exit Code: {exit_code} ---\n"
                output_str += f"--- Duration: {duration}s ---\n"

                structured_results.append({
                    "line": idx,
                    "command": stripped,
                    "stdout": std_out,
                    "stderr": std_err,
                    "exit_code": exit_code,
                    "duration_s": duration
                })
                summary.append(f"[Line {idx}] Exit Code: {exit_code}")
                if stop_on_error and exit_code != 0:
                    output_str += f"Stopping execution due to error (exit code {exit_code}).\n"
                    break
            except subprocess.TimeoutExpired:
                msg = f"--- TIMEOUT ---\nCommand timed out after {timeout} seconds.\n"
                print(msg)
                output_str += msg
                structured_results.append({
                    "line": idx,
                    "command": stripped,
                    "stdout": "",
                    "stderr": msg,
                    "exit_code": -999,
                    "duration_s": float(timeout)
                })
                summary.append(f"[Line {idx}] Timeout")
                if stop_on_error:
                    output_str += "Stopping execution due to timeout.\n"
                    break
            except Exception as e:
                error_message = f"--- EXECUTION ERROR ---\nFailed to execute line {idx}: {e}\n"
                print(error_message)
                output_str += error_message
                structured_results.append({
                    "line": idx,
                    "command": stripped,
                    "stdout": "",
                    "stderr": error_message,
                    "exit_code": -1,
                    "duration_s": 0.0
                })
                summary.append(f"[Line {idx}] Exception")
                if stop_on_error:
                    output_str += "Stopping execution due to exception.\n"
                    break

        if not executed_any:
            output_str += "No commands executed (all lines empty or commented).\n"

        output_str += "\n--- SUMMARY ---\n" + "\n".join(summary) + "\n"

        if structured_output:
            return (json.dumps(structured_results, indent=2),)
        else:
            return (output_str,)

# Node class mappings for ComfyUI
NODE_CLASS_MAPPINGS = {
    "RunCommand (DANGEROUS)": RunCommandNode
}

# Optional: A display name mapping
NODE_DISPLAY_NAME_MAPPINGS = {
    "RunCommand (DANGEROUS)": "⚠️ Run Shell Command (DANGEROUS)"
}