import subprocess
import shlex
import os # Import os for path joining if needed, though not strictly necessary here

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
                    "multiline": True, # Allows multi-line commands
                    "default": "# This is a comment and will be ignored\necho 'Hello from ComfyUI!'"
                }),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("output",)
    FUNCTION = "execute_command"
    CATEGORY = "⚠️Utils/Execution (DANGEROUS)" # Clearly mark as dangerous

    def execute_command(self, command):
        output_str = ""
        if not command or not command.strip():
            message = "Command is empty, ignoring."
            print(message)
            return (message,)

        lines = command.splitlines()
        executed_any = False

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
                    executable='/bin/bash'
                )
                if result.stdout:
                    output_str += f"--- STDOUT ---\n{result.stdout}"
                if result.stderr:
                    output_str += f"--- STDERR ---\n{result.stderr}"
                output_str += f"--- Exit Code: {result.returncode} ---\n"
            except Exception as e:
                error_message = f"--- EXECUTION ERROR ---\nFailed to execute line {idx}: {e}\n"
                print(error_message)
                output_str += error_message

        if not executed_any:
            output_str += "No commands executed (all lines empty or commented).\n"

        return (output_str,)

# Node class mappings for ComfyUI
NODE_CLASS_MAPPINGS = {
    "RunCommand (DANGEROUS)": RunCommandNode
}

# Optional: A display name mapping
NODE_DISPLAY_NAME_MAPPINGS = {
    "RunCommand (DANGEROUS)": "⚠️ Run Shell Command (DANGEROUS)"
}