class CommandLineNode:
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
        return (f"You entered: {command}",)

NODE_CLASS_MGR = None
try:
    from . import NODE_CLASS_MGR
except ImportError:
    pass

if NODE_CLASS_MGR:
    NODE_CLASS_MGR.register_node(CommandLineNode)