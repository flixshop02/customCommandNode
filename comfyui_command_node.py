from nodes import ComfyNode, NODE_CLASS_MGR

class CommandLineNode(ComfyNode):
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"command": ("STRING", {"multiline": False, "default": ""})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("output",)
    FUNCTION = "run_command"
    CATEGORY = "Custom"

    @classmethod
    def NAME(cls):
        return "Run Command"

    def run_command(self, command):
        return (f"You entered: {command}",)

NODE_CLASS_MGR.register_node(CommandLineNode)