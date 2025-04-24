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
        # --- Added Check for '#' ---
        stripped_command = command.strip()
        if not stripped_command: # Also ignore empty commands
            message = "Command is empty, ignoring."
            print(message)
            return (message,)
        if stripped_command.startswith('#'):
            message = f"Command ignored (starts with #): {command}"
            print(message)
            # Return the message as output to indicate it was skipped
            return (message,)
        # --- End of Added Check ---

        print(f"Executing command: {command}")
        output_str = ""
        try:
            # Use shell=True cautiously. It's needed for complex commands with pipes, etc.
            # but increases security risks as the shell interprets the command string.
            # For simpler commands, consider shell=False and splitting the command using shlex.split(command)
            result = subprocess.run(
                command,
                shell=True,
                check=False, # Don't raise exception on non-zero exit code, capture it instead
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, # Capture output as text
                executable='/bin/bash' # Explicitly use bash on Linux
            )

            if result.stdout:
                output_str += f"--- STDOUT ---\n{result.stdout}\n"
                print(f"Command STDOUT:\n{result.stdout}")

            if result.stderr:
                output_str += f"--- STDERR ---\n{result.stderr}\n"
                print(f"Command STDERR:\n{result.stderr}")

            output_str += f"\n--- Exit Code: {result.returncode} ---"
            print(f"Command exited with code: {result.returncode}")

            # Optional: Raise an error in ComfyUI if the command failed
            # if result.returncode != 0:
            #     raise Exception(f"Command failed with exit code {result.returncode}:\n{result.stderr}")

        except Exception as e:
            error_message = f"Failed to execute command: {e}"
            print(error_message)
            output_str += f"\n--- EXECUTION ERROR ---\n{error_message}"
            # Optionally re-raise the exception to halt the workflow
            # raise e

        # Return the combined output/error string
        return (output_str,)

# Node class mappings for ComfyUI
NODE_CLASS_MAPPINGS = {
    "RunCommand (DANGEROUS)": RunCommandNode
}

# Optional: A display name mapping
NODE_DISPLAY_NAME_MAPPINGS = {
    "RunCommand (DANGEROUS)": "⚠️ Run Shell Command (DANGEROUS)"
}