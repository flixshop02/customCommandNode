"""
HTTP routes backing the RunCommand node's snippet library and run history
UI (see web/js/run_command_ui.js). Registered against ComfyUI's own
PromptServer aiohttp app, so these only take effect when running inside
ComfyUI — importing this module outside that context is safe and simply
skips registration.

These routes store/return text only; they can never execute a command by
themselves (execution always goes through the node and its 'armed' check).
They are still unauthenticated like the rest of ComfyUI's API, so don't
expose the server to untrusted clients. POST requires a JSON content type,
which blocks the trivial cross-origin form-post path.
"""

from .command_executor import (
    load_snippets,
    save_snippet,
    delete_snippet,
    read_recent_history,
)

try:
    from aiohttp import web
    from server import PromptServer
    HAVE_SERVER = True
except ImportError:
    HAVE_SERVER = False

MAX_SNIPPETS = 500
MAX_NAME_LEN = 120


def register_routes():
    if not HAVE_SERVER:
        print("[RunCommandNode] PromptServer/aiohttp not available — snippet/history "
              "HTTP routes not registered (expected when running outside ComfyUI).")
        return

    app = PromptServer.instance.app
    routes = web.RouteTableDef()

    @routes.get("/run_command_node/snippets")
    async def get_snippets(request):
        return web.json_response(load_snippets())

    @routes.post("/run_command_node/snippets")
    async def post_snippet(request):
        if request.content_type != "application/json":
            return web.json_response(
                {"error": "Content-Type must be application/json"}, status=415)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"error": "JSON body must be an object"}, status=400)

        name = (data.get("name") or "").strip()
        command = data.get("command") or ""
        working_dir = data.get("working_dir") or ""
        tags = data.get("tags")
        tags = [str(t)[:64] for t in tags][:20] if isinstance(tags, list) else []

        if not name or not command.strip():
            return web.json_response({"error": "name and command are required"}, status=400)
        if len(name) > MAX_NAME_LEN:
            return web.json_response(
                {"error": f"name too long (max {MAX_NAME_LEN} characters)"}, status=400)

        try:
            existing = load_snippets()
        except Exception as e:
            return web.json_response({"error": f"could not read snippets: {e}"}, status=500)
        if len(existing) >= MAX_SNIPPETS and not any(s.get("name") == name for s in existing):
            return web.json_response(
                {"error": f"snippet limit reached ({MAX_SNIPPETS})"}, status=400)

        try:
            snippets = save_snippet(name, command, working_dir, tags)
        except Exception as e:
            return web.json_response({"error": f"failed to save snippet: {e}"}, status=500)
        return web.json_response(snippets)

    @routes.delete("/run_command_node/snippets/{snippet_id}")
    async def delete_snippet_route(request):
        snippet_id = request.match_info["snippet_id"]
        try:
            snippets = delete_snippet(snippet_id)
        except Exception as e:
            return web.json_response({"error": f"failed to delete snippet: {e}"}, status=500)
        return web.json_response(snippets)

    @routes.get("/run_command_node/history")
    async def get_history(request):
        try:
            n = int(request.query.get("n", 20))
        except (TypeError, ValueError):
            n = 20
        return web.json_response(read_recent_history(n))

    app.add_routes(routes)
    print("[RunCommandNode] Registered snippet/history HTTP routes.")
