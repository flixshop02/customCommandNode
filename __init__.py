# RunCommand (DANGEROUS) — ComfyUI custom node (v2).
#
# Registers the node class plus a small frontend extension and two HTTP
# routes (snippet library + run history). See README.md for the safety
# model and the full v2 change list.

import os

# Import the node class mappings and display name mappings
# from the actual node implementation file (command_executor.py).
from .command_executor import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__version__ = "2.1.0"

# Register the snippet-library / run-history HTTP routes against ComfyUI's
# PromptServer. Wrapped in try/except so a failure here (e.g. a very old
# ComfyUI build without the aiohttp app in the expected place) can't take
# down node loading entirely — the node still works, it just loses the
# snippets/history JS panel data.
try:
    from .routes import register_routes
    register_routes()
except Exception as e:
    print(f"[RunCommandNode] Failed to register HTTP routes: {e}")

# Tells ComfyUI to serve this folder's contents so web/js/run_command_ui.js
# gets loaded as a frontend extension (arm/disarm visuals, forced-disarm on
# workflow load, snippet dropdown + save/delete buttons, history viewer).
WEB_DIRECTORY = "web"

if os.environ.get("COMFYUI_RUNCOMMAND_DISABLE") == "1":
    print("[RunCommandNode] COMFYUI_RUNCOMMAND_DISABLE=1 is set: "
          "command execution is disabled.")

# Export the mappings for ComfyUI to discover the nodes
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

print(f"Loaded Custom Command Node v{__version__} "
      "(arm/disarm, async exec, process-tree kill, snippets & history)")
