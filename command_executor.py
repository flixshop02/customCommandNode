import subprocess
import shlex
import os

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
                "env_vars": ("STRING", {
                    "multiline": True,
                    "default": "# KEY=VALUE format, one per line\n"
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
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("output",)
    FUNCTION = "execute_command"
    CATEGORY = "⚠️Utils/Execution (DANGEROUS)"

    def parse_env_vars(self, env_str):
        env = {}
        for line in env_str.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
        return env

    def execute_command(self, command, env_vars, working_dir, stop_on_error, timeout, truncate_output, structured_output):
        output_str = ""
        structured_results = []
        if not command or not command.strip():
            message = "Command is empty, ignoring."
            print(message)
            return (message,)

        lines = command.splitlines()
        executed_any = False
        env = os.environ.copy()
        env.update(self.parse_env_vars(env_vars))
        summary = []
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                output_str += f"[Line {idx}] Skipped: {line}\n"
                continue

            executed_any = True
            output_str += f"\n[Line {idx}] Executing: {stripped}\n"
            print(f"Executing line {idx}: {stripped}")
            try:
                result = subprocess.run(
                    stripped,
                    shell=True,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    executable='/bin/bash',
                    cwd=working_dir if working_dir else None,
                    env=env,
                    timeout=timeout
                )
                std_out = result.stdout or ""
                std_err = result.stderr or ""
                exit_code = result.returncode
                # Truncate output if needed
                if len(std_out) > truncate_output:
                    std_out = std_out[:truncate_output] + "\n... (truncated)\n"
                if len(std_err) > truncate_output:
                    std_err = std_err[:truncate_output] + "\n... (truncated)\n"
                output_str += f"--- STDOUT ---\n{std_out}"
                output_str += f"--- STDERR ---\n{std_err}"
                output_str += f"--- Exit Code: {exit_code} ---\n"
                structured_results.append({
                    "line": idx,
                    "command": stripped,
                    "stdout": std_out,
                    "stderr": std_err,
                    "exit_code": exit_code
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
                    "exit_code": -999
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
                    "exit_code": -1
                })
                summary.append(f"[Line {idx}] Exception")
                if stop_on_error:
                    output_str += "Stopping execution due to exception.\n"
                    break

        if not executed_any:
            output_str += "No commands executed (all lines empty or commented).\n"

        output_str += "\n--- SUMMARY ---\n" + "\n".join(summary) + "\n"

        if structured_output:
            import json
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