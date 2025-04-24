# Import the node class mappings and display name mappings
# from your actual node implementation file (e.g., command_executor.py).
# Make sure the filename here matches the file containing your node class.
from .command_executor import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# Export the mappings for ComfyUI to discover the nodes
__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']

print("✅ Loaded Custom Command Node") # Optional: Confirmation message