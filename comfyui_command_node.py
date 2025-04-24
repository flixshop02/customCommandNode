import subprocess
from . import NODE_CLASS_MGR, NODE_INPUT_TYPES, NODE_OUTPUT_TYPES, ComfyNode

class CommandLineNode(ComfyNode):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "command": ("STRING", {"multiline": False, "default": ""}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("output",)
    FUNCTION = "run_command"
    CATEGORY = "Custom"

    def run_command(self, command):
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60
            )
            output = f"Return code: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        except Exception as e:
            output = f"Exception: {str(e)}"
        return (output,)

NODE_CLASS_MGR.register_node(CommandLineNode)