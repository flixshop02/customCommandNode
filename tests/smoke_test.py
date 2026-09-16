"""
Standalone smoke test for the RunCommand node (v2).

Runs without ComfyUI. All storage (snippets, history, overflow) is
redirected to a temporary folder, so your real data is never touched.

Usage:
    python tests/smoke_test.py
"""

import asyncio
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "run_command_node_exec", ROOT / "command_executor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    m = load_module()

    tmp = Path(tempfile.mkdtemp(prefix="runcommand-test-"))
    # Redirect all persistent storage into the temp folder.
    m._user_data_dir = lambda: tmp

    is_win = m.IS_WINDOWS
    exit_fail = "exit /b 3" if is_win else "exit 3"
    chain_fail = "exit /b 2" if is_win else "exit 2"
    sleep_cmd = "ping -n 6 127.0.0.1" if is_win else "sleep 30"

    async def run(node, **kw):
        kw.setdefault("armed", True)
        kw.setdefault("working_dir", "")
        kw.setdefault("stop_on_error", False)
        kw.setdefault("timeout", 15)
        kw.setdefault("structured_output", False)
        kw.setdefault("chain_commands", False)
        return await node.execute_command(**kw)

    async def run_all():
        node = m.RunCommandNode()

        # 1. Disarmed refuses to run.
        out, logs = await run(node, armed=False, command="echo should-not-run")
        assert "DISARMED" in out, out
        assert "mode: disarmed" in logs, logs
        print("[1] disarmed refusal OK")

        # 2. Line mode + stop_on_error.
        out, logs = await run(
            node,
            command=f"# comment\necho hello\necho boom 1>&2\n{exit_fail}\necho after",
            stop_on_error=True)
        assert "hello" in out and "boom" in out, out
        assert "after" not in out, out
        assert "exit_code: 3" in logs, logs
        print("[2] line mode + stop_on_error OK")

        # 3. Chained mode runs all lines (comment lines included).
        out, _ = await run(node, command="# first comment\necho one\necho two",
                           chain_commands=True)
        assert "one" in out and "two" in out, out
        print("[3] chained mode OK")

        # 4. Chained mode + stop_on_error stops early.
        out, _ = await run(node, command=f"echo ok\n{chain_fail}\necho never",
                           chain_commands=True, stop_on_error=True)
        assert "ok" in out and "never" not in out, out
        print("[4] chained mode + stop_on_error OK")

        # 5. Structured output is valid JSON.
        out, _ = await run(node, command="echo s1\necho s2", structured_output=True)
        data = json.loads(out)
        assert len(data) == 2 and data[0]["exit_code"] == 0, data
        print("[5] structured output OK")

        # 6. Timeout kills the process promptly.
        t0 = time.time()
        out, _ = await run(node, command=sleep_cmd, timeout=1)
        elapsed = time.time() - t0
        assert "TIMEOUT" in out, out
        assert elapsed < 10, elapsed
        print(f"[6] timeout + kill OK ({elapsed:.1f}s)")

        # 7. Empty command is refused.
        out, _ = await run(node, command="   \n# only comments\n")
        assert "No commands executed" in out, out
        print("[7] empty command OK")

        print("ALL SMOKE TESTS PASSED")

    def storage_tests():
        # extra_env validation
        env = {"A": "1"}
        m._merge_extra_env(env, "GOOD=yes\n=nope\n  =nope\n# comment\n")
        assert env["GOOD"] == "yes" and "" not in env, env

        # Snippet save / upsert / delete
        m.save_snippet("greet", "echo hi")
        m.save_snippet("greet", "echo hello")
        snippets = m.load_snippets()
        assert len([s for s in snippets if s["name"] == "greet"]) == 1, snippets
        assert snippets[0]["command"] == "echo hello", snippets
        m.delete_snippet(snippets[0]["id"])
        assert m.load_snippets() == []

        # Corrupt snippets file is moved aside, not silently wiped
        (tmp / "snippets.json").write_text("{not json", encoding="utf-8")
        assert m.load_snippets() == []
        assert list(tmp.glob("snippets.json.bad-*")), "backup missing"

        # History rotation and clamping
        m.MAX_HISTORY_BYTES = 30
        m.MAX_HISTORY_LINES = 3
        for i in range(10):
            m.append_history({"i": i})
        hist = m.read_recent_history(10)
        assert len(hist) <= 3, hist
        assert isinstance(m.read_recent_history(0), list)
        assert isinstance(m.read_recent_history("bogus"), list)
        print("[8] storage (snippets/history/env) OK")

    try:
        asyncio.run(run_all())
        storage_tests()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
