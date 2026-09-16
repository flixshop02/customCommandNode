"""
RunCommand node for ComfyUI (v2).

EXTREME SECURITY RISK
=====================

This node executes arbitrary shell commands on the machine running ComfyUI.
It is designed ONLY for a single-user, local ComfyUI install. It is NOT
hardened for multi-tenant or internet-exposed servers.

Safety model (read this before using):
  - Nothing runs unless 'armed' is True. It defaults to False, and the
    bundled frontend extension (web/js/run_command_ui.js) forces it back to
    False every time a workflow is loaded, so a shared/downloaded workflow
    cannot silently carry a pre-armed command into your session.
  - That arm/disarm mechanism is a guardrail, NOT a security boundary.
    Anything that can queue a prompt (the REST API, a script, this node
    loaded without the JS extension) can pass armed=True. If you need a
    hard server-side off switch, set COMFYUI_RUNCOMMAND_DISABLE=1 before
    starting ComfyUI, or simply do not expose ComfyUI to untrusted clients.
  - COMFYUI_RUNCOMMAND_DISABLE=1 disables execution entirely, regardless of
    the per-node toggle.
  - In line mode, lines that are empty or start with '#' are skipped. In
    chained mode the command is written to a real script, so '#' lines are
    native comments on POSIX; on Windows they are rewritten to REM lines.

Execution model:
  - Async (ComfyUI awaits coroutine node functions), so the server stays
    responsive while a command runs.
  - Line mode runs each non-comment line in its own shell, each with its own
    timeout. Chained mode writes all lines to one temporary script and runs
    it once; the timeout applies to the whole script.
  - Timeout and the ComfyUI Cancel button both kill the whole process tree
    (psutil when installed; otherwise POSIX process-group kill or Windows
    `taskkill /T /F`), so backgrounded children cannot survive as orphans.
  - Output is captured incrementally with a per-stream soft cap; overflow is
    appended to disk under <ComfyUI user dir>/run_command_node/overflow/.
    The aggregated transcript, the structured-output payload and the history
    file are capped too, so a runaway command cannot balloon memory, the
    websocket or the returned strings.

v2 fixes over the previous overhaul (see README.md for the full list):
  - OUTPUT_NODE = True plus an always-changed IS_CHANGED, so the node
    actually executes on every queue instead of being skipped/ignored.
  - 'snippet' is a plain STRING input, so a deleted/renamed/imported snippet
    name can no longer make the whole prompt fail "Value not in list".
  - Chained mode no longer strips '#' lines on POSIX (heredocs safe), and
    respects stop_on_error via `set -e -o pipefail` (`if errorlevel` guards
    on Windows).
  - Bounded, rotating run history; pruned overflow files; non-blocking
    taskkill; throttled websocket logging; compact status returned in the
    'logs' output; accurate documentation.
"""

import asyncio
import json
import os
import signal
import subprocess
import tempfile
import time
import traceback
import uuid
from collections import deque
from pathlib import Path

# ---------------------------------------------------------------------------
# Optional dependency: psutil gives us reliable cross-platform "kill this
# process and all its descendants" without hand-rolled platform branching.
# We degrade gracefully if it is not installed (POSIX process-group kill /
# Windows `taskkill /T /F` still work — see requirements.txt).
# ---------------------------------------------------------------------------
try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False

IS_WINDOWS = os.name == "nt"

# ---------------------------------------------------------------------------
# Best-effort ComfyUI websocket hook, for optional live log streaming.
# ---------------------------------------------------------------------------
try:
    from server import PromptServer
    HAVE_PROMPT_SERVER = True
except ImportError:
    HAVE_PROMPT_SERVER = False

DISABLE_ENV = "COMFYUI_RUNCOMMAND_DISABLE"

# Per-stream soft cap kept in memory; further output still goes to the
# overflow file on disk (and to throttled websocket log messages).
SOFT_OUTPUT_CAP_BYTES = 512 * 1024
# Aggregate caps, so one long script cannot accumulate unbounded strings.
MAX_RETURNED_CHARS = 2 * 1024 * 1024
MAX_STRUCTURED_BYTES = 2 * 1024 * 1024
# Run history rotation.
MAX_HISTORY_BYTES = 2 * 1024 * 1024
MAX_HISTORY_LINES = 5000
# Overflow file housekeeping.
MAX_OVERFLOW_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_OVERFLOW_FILES = 200


def _log_send(event, data):
    """Push a message over the ComfyUI websocket. No-ops safely if
    PromptServer isn't importable or nothing is listening."""
    if not HAVE_PROMPT_SERVER:
        return
    try:
        PromptServer.instance.send_sync(event, data)
    except Exception:
        pass


def _is_interrupted() -> bool:
    """Best-effort check of ComfyUI's own cancel/interrupt signal, so hitting
    the UI's Cancel button also kills our subprocess tree instead of just
    abandoning it. The exact API has moved between ComfyUI versions, so every
    lookup here is defensive; if none match, the timeout is the only way to
    kill a stuck command, which always works regardless."""
    try:
        import comfy.model_management as mm
        if hasattr(mm, "processing_interrupted"):
            return bool(mm.processing_interrupted())
    except Exception:
        pass
    # Legacy fallbacks for older ComfyUI builds.
    try:
        import execution
        flag = getattr(execution, "interrupt_processing", None)
        if isinstance(flag, bool):
            return flag
    except Exception:
        pass
    return False


class _CancelledByUser(Exception):
    """Raised internally when ComfyUI's cancel/interrupt signal fires
    mid-command, so it can be routed through the same kill-the-tree logic
    as a timeout."""
    pass


# ---------------------------------------------------------------------------
# Persistent storage (snippets + run history)
# ---------------------------------------------------------------------------

def _user_data_dir() -> Path:
    """Resolve ComfyUI's per-user data folder for this node's storage,
    falling back to a local folder next to this file if ComfyUI's
    folder_paths module isn't importable (e.g. running this file outside
    ComfyUI, such as during a syntax check)."""
    try:
        import folder_paths
        base = Path(folder_paths.get_user_directory())
    except Exception:
        base = Path(__file__).parent / "_user_data"
    d = base / "run_command_node"
    d.mkdir(parents=True, exist_ok=True)
    return d


_paths_cache = None


def _paths():
    """Resolve storage paths lazily (and only once), so importing this module
    never touches the filesystem."""
    global _paths_cache
    if _paths_cache is None:
        base = _user_data_dir()
        _paths_cache = (base / "snippets.json", base / "history.jsonl")
    return _paths_cache


def snippets_path() -> Path:
    return _paths()[0]


def history_path() -> Path:
    return _paths()[1]


def _atomic_write_text(path: Path, text: str):
    """Write text atomically (temp file + os.replace) so a crash mid-write
    never corrupts the file."""
    tmp = path.with_name(path.name + f".tmp-{uuid.uuid4().hex}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data):
    _atomic_write_text(path, json.dumps(data, indent=2))


def load_snippets():
    """Load the snippet library. A corrupt file is moved aside (with a
    timestamped .bad-* suffix) instead of being silently overwritten, so
    the user can recover it."""
    path = snippets_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [s for s in data if isinstance(s, dict)]
        raise ValueError("snippets.json is not a list")
    except Exception as e:
        print(f"[RunCommandNode] Could not read snippets file ({e}); "
              f"moving it aside so it is not overwritten.")
        try:
            backup = path.with_name(path.name + f".bad-{int(time.time())}")
            os.replace(path, backup)
        except Exception:
            pass
        return []


def save_snippet(name, command, working_dir="", tags=None):
    """Save a snippet. If a snippet with the same name exists it is updated
    in place (names are the UI key, so duplicates are confusing)."""
    snippets = load_snippets()
    entry = None
    for s in snippets:
        if s.get("name") == name:
            entry = s
            break
    if entry is None:
        entry = {"id": uuid.uuid4().hex[:8], "name": name, "created_at": time.time()}
        snippets.append(entry)
    entry["command"] = command
    entry["working_dir"] = working_dir or ""
    entry["tags"] = list(tags or [])
    entry["updated_at"] = time.time()
    _atomic_write_json(snippets_path(), snippets)
    return snippets


def delete_snippet(snippet_id):
    snippets = [s for s in load_snippets() if s.get("id") != snippet_id]
    _atomic_write_json(snippets_path(), snippets)
    return snippets


def _trim_history():
    """Keep history.jsonl bounded: once it grows past MAX_HISTORY_BYTES,
    rewrite it with only the newest MAX_HISTORY_LINES lines."""
    path = history_path()
    try:
        if not path.exists():
            return
        if path.stat().st_size <= MAX_HISTORY_BYTES:
            return
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            kept = deque(f, maxlen=MAX_HISTORY_LINES)
        _atomic_write_text(path, "".join(kept))
    except Exception as e:
        print(f"[RunCommandNode] Failed to trim history: {e}")


def append_history(entry: dict):
    try:
        with open(history_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[RunCommandNode] Failed to write history: {e}")
        return
    _trim_history()


def read_recent_history(n=20):
    """Return the last n history entries. Reads only a bounded tail of the
    file, and clamps n to a sane range."""
    try:
        n = max(1, min(int(n), 200))
    except (TypeError, ValueError):
        n = 20
    path = history_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=n)
        return [json.loads(line) for line in tail if line.strip()]
    except Exception:
        return []


def _prune_overflow(directory: Path):
    """Best-effort cleanup of old/excess overflow files. Never touches files
    from the current run (those sort newest and/or are recent)."""
    try:
        files = [p for p in directory.iterdir() if p.is_file()]
    except Exception:
        return
    try:
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return
    cutoff = time.time() - MAX_OVERFLOW_AGE_SECONDS
    for i, p in enumerate(files):
        try:
            if i >= MAX_OVERFLOW_FILES or p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Process-tree killing
# ---------------------------------------------------------------------------

async def _kill_process_tree(proc, grace_period=3.0):
    """Kill proc and every descendant it spawned. Tries psutil first (cleanest
    cross-platform descendant tracking), falls back to POSIX process-group
    kill or Windows `taskkill /T /F`."""
    if proc.returncode is not None:
        return  # already dead

    pid = proc.pid

    if HAVE_PSUTIL:
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            procs = children + [parent]
            for p in procs:
                try:
                    p.terminate()
                except psutil.NoSuchProcess:
                    pass
            gone, alive = psutil.wait_procs(procs, timeout=grace_period)
            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
            return
        except psutil.NoSuchProcess:
            return
        except Exception as e:
            print(f"[RunCommandNode] psutil kill failed, falling back: {e}")

    # --- Fallback without psutil ---
    if IS_WINDOWS:
        # Non-blocking: a synchronous subprocess.run() here would stall the
        # whole ComfyUI event loop for the duration of the kill.
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/T", "/F", "/PID", str(pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(killer.wait(), timeout=grace_period + 2)
            except asyncio.TimeoutError:
                try:
                    killer.kill()
                except Exception:
                    pass
        except Exception as e:
            print(f"[RunCommandNode] taskkill failed: {e}")
        # Safety net in case taskkill itself failed: kill at least the shell.
        try:
            if proc.returncode is None:
                proc.kill()
        except Exception:
            pass
    else:
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace_period)
            except asyncio.TimeoutError:
                os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            print(f"[RunCommandNode] killpg failed: {e}")
            try:
                proc.kill()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Output capture helpers
# ---------------------------------------------------------------------------

class CappedBuffer:
    """Per-stream capture with a soft in-memory cap. Overflow (everything,
    all the time — the file is the authoritative full log) is appended to
    disk so nothing is lost, while memory stays bounded."""

    def __init__(self, cap_bytes=SOFT_OUTPUT_CAP_BYTES, overflow_path=None):
        self.cap_bytes = cap_bytes
        self.chunks = []
        self.size = 0
        self.truncated = False
        self.overflow_path = overflow_path
        self._overflow_fh = None

    def add(self, text: str):
        if not text:
            return
        if self.size < self.cap_bytes:
            self.chunks.append(text)
            self.size += len(text.encode("utf-8", errors="ignore"))
        else:
            self.truncated = True
        if self.overflow_path:
            try:
                if self._overflow_fh is None:
                    self._overflow_fh = open(self.overflow_path, "a", encoding="utf-8")
                self._overflow_fh.write(text)
                self._overflow_fh.flush()
            except Exception:
                pass

    def get(self) -> str:
        text = "".join(self.chunks)
        if self.truncated:
            text += f"\n... [output truncated after {self.cap_bytes // 1024}KB in memory"
            if self.overflow_path:
                text += f"; full output at {self.overflow_path}"
            text += "] ...\n"
        return text

    def close(self):
        if self._overflow_fh:
            try:
                self._overflow_fh.close()
            except Exception:
                pass
            self._overflow_fh = None


class Transcript:
    """Aggregated human-readable output with an overall cap, so one long
    script cannot accumulate unbounded strings (the per-stream CappedBuffers
    only cap each individual stream)."""

    def __init__(self, limit_chars=MAX_RETURNED_CHARS):
        self.limit_chars = limit_chars
        self._parts = []
        self._size = 0
        self.dropped_chars = 0

    def add(self, text: str):
        if not text:
            return
        if self._size < self.limit_chars:
            self._parts.append(text)
            self._size += len(text)
        else:
            self.dropped_chars += len(text)

    def get(self) -> str:
        text = "".join(self._parts)
        if self.dropped_chars:
            text += (f"\n... [transcript truncated: {self.dropped_chars} further "
                     f"characters were dropped; full per-stream output is in the "
                     f"run's overflow files] ...\n")
        return text


class StructuredSink:
    """Bounded collector for structured (JSON) results."""

    def __init__(self, limit_bytes=MAX_STRUCTURED_BYTES):
        self.limit_bytes = limit_bytes
        self.items = []
        self._size = 0
        self.truncated = False

    def add(self, entry: dict):
        size = len(entry.get("stdout") or "") + len(entry.get("stderr") or "")
        if self._size + size > self.limit_bytes:
            if not self.truncated:
                self.truncated = True
                self.items.append({
                    "note": ("structured output truncated: further results "
                             "were dropped; full per-stream output is in the "
                             "run's overflow files"),
                })
            return
        self._size += size
        self.items.append(entry)


class _LiveLog:
    """Coalesces live websocket log output: a chatty command producing
    thousands of 4KB chunks would otherwise flood the socket. Chunks are
    batched and flushed at most every FLUSH_INTERVAL seconds (or sooner if
    the buffer gets big)."""

    FLUSH_INTERVAL = 0.15
    MAX_BUFFER_CHARS = 32 * 1024

    def __init__(self, run_id, unique_id, tag):
        self.run_id = run_id
        self.unique_id = unique_id
        self.tag = tag
        self._buffers = {}
        self._last_flush = 0.0

    def feed(self, stream: str, text: str):
        if not text or not HAVE_PROMPT_SERVER:
            return
        buf = self._buffers.setdefault(stream, [])
        buf.append(text)
        buffered = sum(len(c) for c in buf)
        if buffered >= self.MAX_BUFFER_CHARS or \
                (time.monotonic() - self._last_flush) >= self.FLUSH_INTERVAL:
            self.flush()

    def flush(self):
        for stream, chunks in self._buffers.items():
            if not chunks:
                continue
            _log_send("runcommand.log", {
                "run_id": self.run_id,
                "node": self.unique_id,
                "tag": self.tag,
                "stream": stream,
                "text": "".join(chunks),
            })
            chunks.clear()
        self._last_flush = time.monotonic()


# ---------------------------------------------------------------------------
# The node
# ---------------------------------------------------------------------------

class RunCommandNode:
    """
    EXTREME SECURITY RISK
    This node executes arbitrary shell commands on the server running ComfyUI.
    Use with extreme caution and only in completely isolated, trusted
    environments (single-user, local machine).

    Safety model (read this before using):
      - The node does nothing unless 'armed' is True.
      - 'armed' is forced back to False every time you load/open a workflow
        (by the bundled frontend extension), even if the saved workflow file
        has it set to True. You must consciously re-arm it every session.
      - This guardrail is not a security boundary: anything that can queue a
        prompt directly (REST API, script) can still pass armed=True. Set
        COMFYUI_RUNCOMMAND_DISABLE=1 before starting ComfyUI for a hard,
        server-side off switch.
      - In line mode, commands starting with '#' are comments and ignored.
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "armed": ("BOOLEAN", {
                    "default": False,
                    "label_on": "ARMED - will execute",
                    "label_off": "disarmed - safe",
                }),
                "command": ("STRING", {
                    "multiline": True,
                    "default": "# This is a comment and will be ignored\necho 'Hello from ComfyUI!'",
                }),
                "working_dir": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Optional: working directory",
                }),
                "stop_on_error": ("BOOLEAN", {
                    "default": False,
                }),
                "timeout": ("INT", {
                    "default": 30,
                    "min": 1,
                    "max": 600,
                    "step": 1,
                    "label": "Timeout in seconds (per line; whole script in chained mode)",
                }),
                "structured_output": ("BOOLEAN", {
                    "default": False,
                }),
                "chain_commands": ("BOOLEAN", {
                    "default": False,
                    "label": "Chain commands (run as one shell script)",
                }),
            },
            "optional": {
                # Deliberately a plain STRING, not a combo: this value is
                # UI-only (the frontend upgrades it to a dropdown and copies
                # the selected snippet's text into 'command'). A combo would
                # be validated against the server's current snippet list at
                # queue time, so a deleted/renamed/imported snippet name could
                # fail the whole prompt with "Value not in list".
                "snippet": ("STRING", {
                    "default": "(none)",
                    "multiline": False,
                    "placeholder": "Snippet picker (UI only)",
                }),
                "extra_env": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "KEY=value lines merged into the command's environment",
                }),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("output", "logs")
    FUNCTION = "execute_command"
    CATEGORY = "Utils/Execution (DANGEROUS)"
    DESCRIPTION = (
        "Runs shell commands on the ComfyUI host when queued. Armed by "
        "default OFF and disarmed on every workflow load. Read the node "
        "docs before use - this is arbitrary code execution by design."
    )
    # Mark as an output node so it always executes when the workflow is
    # queued, even when its STRING outputs are not connected to anything
    # (ComfyUI normally only executes output nodes and their ancestors).
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Side-effect node: always re-run, never serve a cached output.
        return float("NaN")

    # -- entry point -----------------------------------------------------

    async def execute_command(self, armed, command, working_dir, stop_on_error,
                              timeout, structured_output, chain_commands,
                              snippet="(none)", extra_env="", unique_id=None):
        # 'snippet' is intentionally unused server-side: it is a UI helper
        # whose value was already copied into 'command' in the browser.
        run_id = uuid.uuid4().hex[:12]
        started_at = time.time()

        # Global kill switch: independent of the per-node toggle.
        if os.environ.get(DISABLE_ENV) == "1":
            message = (f"RunCommand is globally disabled via {DISABLE_ENV}=1. "
                       f"Unset that env var (and restart ComfyUI) to re-enable.")
            print(f"[RunCommandNode] {message}")
            return (message, self._summary(run_id, "disabled", None, 0, False))

        # Arm/Disarm: the single most important safety check. This must run
        # before ANY other validation or subprocess touch.
        if not armed:
            message = "Node is DISARMED - command not executed. Flip 'armed' to run it."
            print(f"[RunCommandNode] {message}")
            return (message, self._summary(run_id, "disarmed", None, 0, False))

        if not command or not command.strip():
            message = "Command is empty, ignoring."
            print(f"[RunCommandNode] {message}")
            return (message, self._summary(run_id, "empty", None, 0, False))

        if working_dir:
            working_dir = os.path.abspath(os.path.expanduser(working_dir))
            if not os.path.isdir(working_dir):
                message = f"Working directory does not exist: {working_dir}"
                print(f"[RunCommandNode] {message}")
                return (message, self._summary(run_id, "bad-working-dir", None, 0, False))

        env = os.environ.copy()
        _merge_extra_env(env, extra_env)

        overflow_dir = _user_data_dir() / "overflow"
        try:
            overflow_dir.mkdir(parents=True, exist_ok=True)
            _prune_overflow(overflow_dir)
        except Exception:
            pass
        overflow_base = overflow_dir / run_id

        try:
            if chain_commands:
                output_str, structured_items, exit_code, truncated = await self._run_chained(
                    command, working_dir, timeout, env, stop_on_error,
                    run_id, overflow_base, unique_id,
                )
            else:
                output_str, structured_items, exit_code, truncated = await self._run_lines(
                    command, working_dir, timeout, env, stop_on_error,
                    run_id, overflow_base, unique_id,
                )
        except asyncio.CancelledError:
            # ComfyUI cancelled the whole task; let it propagate.
            raise
        except Exception as e:
            traceback.print_exc()
            duration_ms = int((time.time() - started_at) * 1000)
            self._record_history(run_id, command, working_dir, chain_commands,
                                 exit_code=-1, duration_ms=duration_ms,
                                 truncated=False, note="exception")
            message = f"--- EXECUTION ERROR ---\n{type(e).__name__}: {e}\n"
            return (message, self._summary(run_id, "exception", -1, duration_ms, False))

        duration_ms = int((time.time() - started_at) * 1000)
        self._record_history(run_id, command, working_dir, chain_commands,
                             exit_code=exit_code, duration_ms=duration_ms,
                             truncated=truncated)

        mode = "chained" if chain_commands else "lines"
        logs_str = self._summary(run_id, mode, exit_code, duration_ms,
                                 truncated,
                                 overflow_dir if truncated else None)
        if structured_output:
            return (json.dumps(structured_items, indent=2), logs_str)
        return (output_str, logs_str)

    # -- subprocess plumbing ---------------------------------------------

    async def _spawn_shell(self, cmd_str, working_dir, env):
        kwargs = dict(
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir if working_dir else None,
            env=env,
        )
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            return await asyncio.create_subprocess_shell(cmd_str, **kwargs)
        else:
            kwargs["executable"] = "/bin/bash"
            kwargs["start_new_session"] = True  # own process group -> killpg works
            return await asyncio.create_subprocess_shell(cmd_str, **kwargs)

    async def _pump_and_wait(self, proc, out_buf, err_buf, timeout, run_id,
                             unique_id, tag):
        """Read stdout/stderr incrementally while waiting for the process to
        exit, polling for a ComfyUI-side cancel signal or our own timeout so
        either can interrupt a stuck command promptly."""
        logger = _LiveLog(run_id, unique_id, tag)

        async def pump(stream, buf, stream_name):
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                buf.add(text)
                logger.feed(stream_name, text)

        stdout_task = asyncio.create_task(pump(proc.stdout, out_buf, "stdout"))
        stderr_task = asyncio.create_task(pump(proc.stderr, err_buf, "stderr"))
        wait_task = asyncio.create_task(proc.wait())

        deadline = time.monotonic() + timeout
        poll_interval = 0.25

        try:
            while True:
                await asyncio.wait(
                    {stdout_task, stderr_task, wait_task},
                    timeout=poll_interval,
                    return_when=asyncio.ALL_COMPLETED,
                )
                if stdout_task.done() and stderr_task.done() and wait_task.done():
                    break
                if time.monotonic() > deadline:
                    raise asyncio.TimeoutError()
                if _is_interrupted():
                    raise _CancelledByUser()
        except asyncio.CancelledError:
            # Hard cancellation of this node's task (not the cooperative
            # interrupt path above): make sure the process tree dies with us.
            try:
                await _kill_process_tree(proc)
            except BaseException:
                pass
            raise
        finally:
            for t in (stdout_task, stderr_task, wait_task):
                if not t.done():
                    t.cancel()
            logger.flush()

        return proc.returncode

    # -- individual-line mode ---------------------------------------------

    async def _run_lines(self, command, working_dir, timeout, env, stop_on_error,
                         run_id, overflow_base, unique_id):
        transcript = Transcript()
        structured = StructuredSink()
        summary = []
        executed = 0
        exit_code = None
        any_truncated = False

        for idx, line in enumerate(command.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                transcript.add(f"[Line {idx}] Skipped: {line}\n")
                continue

            executed += 1
            header = f"\n[Line {idx}] Executing: {stripped}\n"
            transcript.add(header)
            print(f"[RunCommandNode] Executing line {idx}: {stripped}")

            out_buf = CappedBuffer(overflow_path=f"{overflow_base}.line{idx}.out")
            err_buf = CappedBuffer(overflow_path=f"{overflow_base}.line{idx}.err")

            try:
                proc = await self._spawn_shell(stripped, working_dir, env)
            except Exception as e:
                error_message = f"--- EXECUTION ERROR ---\nFailed to execute line {idx}: {e}\n"
                print(f"[RunCommandNode] {error_message}")
                out_buf.close()
                err_buf.close()
                transcript.add(error_message)
                structured.add({"line": idx, "command": stripped, "stdout": "",
                                "stderr": error_message, "exit_code": -1})
                summary.append(f"[Line {idx}] Exception")
                if stop_on_error:
                    transcript.add("Stopping execution due to exception.\n")
                    break
                continue

            timed_out = False
            cancelled = False
            try:
                exit_code = await self._pump_and_wait(
                    proc, out_buf, err_buf, timeout, run_id, unique_id, idx)
            except asyncio.TimeoutError:
                timed_out = True
                exit_code = -999
                await _kill_process_tree(proc)
            except _CancelledByUser:
                cancelled = True
                exit_code = -998
                await _kill_process_tree(proc)

            std_out = out_buf.get()
            std_err = err_buf.get()
            any_truncated = any_truncated or out_buf.truncated or err_buf.truncated
            out_buf.close()
            err_buf.close()

            if timed_out:
                print(f"[RunCommandNode] Line {idx} timed out after {timeout} seconds.")
                std_err += (f"--- TIMEOUT ---\nCommand timed out after {timeout} "
                            f"seconds.\n")
                summary.append(f"[Line {idx}] Timeout")
            elif cancelled:
                print(f"[RunCommandNode] Line {idx} cancelled from ComfyUI.")
                std_err += "--- CANCELLED ---\nExecution cancelled from ComfyUI.\n"
                summary.append(f"[Line {idx}] Cancelled")
            else:
                summary.append(f"[Line {idx}] Exit Code: {exit_code}")

            transcript.add(f"--- STDOUT ---\n{std_out}")
            transcript.add(f"--- STDERR ---\n{std_err}")
            if not timed_out and not cancelled:
                transcript.add(f"--- Exit Code: {exit_code} ---\n")
            structured.add({"line": idx, "command": stripped, "stdout": std_out,
                            "stderr": std_err, "exit_code": exit_code})

            if cancelled:
                break
            if stop_on_error and (timed_out or exit_code != 0):
                reason = "timeout" if timed_out else f"error (exit code {exit_code})"
                transcript.add(f"Stopping execution due to {reason}.\n")
                break

        if executed == 0:
            transcript.add("No commands executed (all lines empty or commented).\n")

        transcript.add("\n--- SUMMARY ---\n" + "\n".join(summary) + "\n")
        truncated = any_truncated or structured.truncated or transcript.dropped_chars > 0
        return transcript.get(), structured.items, exit_code, truncated

    # -- chained (single script) mode -------------------------------------

    def _write_script(self, lines, stop_on_error):
        suffix = ".bat" if IS_WINDOWS else ".sh"
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=suffix,
                                         encoding="utf-8") as f:
            if IS_WINDOWS:
                f.write("@echo off\n")
                for line in lines:
                    if line.strip().startswith("#"):
                        # '#' is not a cmd comment; rewrite to REM instead of
                        # deleting, so line structure is preserved.
                        line = "REM " + line.strip().lstrip("#").strip()
                    f.write(line + "\n")
                    if stop_on_error and line.strip() and not line.strip().upper().startswith(("REM", "@")):
                        f.write("if errorlevel 1 exit /b %errorlevel%\n")
            else:
                # Lines are written verbatim: '#' is a native bash comment, and
                # leaving them in place keeps heredocs / inline scripts intact.
                if stop_on_error:
                    f.write("set -e\nset -o pipefail\n")
                f.write("\n".join(lines) + "\n")
            path = f.name
        if not IS_WINDOWS:
            os.chmod(path, 0o700)
        return path

    async def _run_chained(self, command, working_dir, timeout, env, stop_on_error,
                           run_id, overflow_base, unique_id):
        transcript = Transcript()
        structured = StructuredSink()
        lines = command.splitlines()

        has_work = any(l.strip() and not l.strip().startswith("#") for l in lines)
        if not has_work:
            transcript.add("No commands executed (all lines empty or commented).\n")
            transcript.add("\n--- SUMMARY ---\n(no commands)\n")
            return transcript.get(), structured.items, None, False

        summary = []
        script_path = None
        out_buf = None
        err_buf = None
        exit_code = None
        truncated = False

        try:
            script_path = self._write_script(lines, stop_on_error)
            transcript.add(f"Executing as shell script: {script_path}\n")

            kwargs = dict(stdout=asyncio.subprocess.PIPE,
                          stderr=asyncio.subprocess.PIPE,
                          cwd=working_dir if working_dir else None,
                          env=env)
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                proc = await asyncio.create_subprocess_exec(
                    "cmd.exe", "/d", "/c", script_path, **kwargs)
            else:
                kwargs["start_new_session"] = True
                proc = await asyncio.create_subprocess_exec(
                    "/bin/bash", script_path, **kwargs)

            print(f"[RunCommandNode] Executing shell script: {script_path}")

            out_buf = CappedBuffer(overflow_path=f"{overflow_base}.chain.out")
            err_buf = CappedBuffer(overflow_path=f"{overflow_base}.chain.err")

            timed_out = False
            cancelled = False
            try:
                exit_code = await self._pump_and_wait(
                    proc, out_buf, err_buf, timeout, run_id, unique_id, "chain")
            except asyncio.TimeoutError:
                timed_out = True
                exit_code = -999
                await _kill_process_tree(proc)
            except _CancelledByUser:
                cancelled = True
                exit_code = -998
                await _kill_process_tree(proc)

            std_out = out_buf.get()
            std_err = err_buf.get()
            truncated = out_buf.truncated or err_buf.truncated

            if timed_out:
                print(f"[RunCommandNode] Shell script timed out after {timeout} seconds.")
                std_err += f"--- TIMEOUT ---\nShell script timed out after {timeout} seconds.\n"
                summary.append("Shell Script Timeout")
            elif cancelled:
                print("[RunCommandNode] Shell script cancelled from ComfyUI.")
                std_err += "--- CANCELLED ---\nExecution cancelled from ComfyUI.\n"
                summary.append("Shell Script Cancelled")
            else:
                summary.append(f"Shell Script Exit Code: {exit_code}")

            transcript.add(f"--- STDOUT ---\n{std_out}")
            transcript.add(f"--- STDERR ---\n{std_err}")
            if not timed_out and not cancelled:
                transcript.add(f"--- Exit Code: {exit_code} ---\n")
            structured.add({"script": script_path, "stdout": std_out,
                            "stderr": std_err, "exit_code": exit_code})

        except Exception as e:
            traceback.print_exc()
            error_message = f"--- EXECUTION ERROR ---\nFailed to execute shell script: {e}\n"
            transcript.add(error_message)
            structured.add({"script": script_path or "", "stdout": "",
                            "stderr": error_message, "exit_code": -1})
            summary.append("Shell Script Exception")
            exit_code = -1
        finally:
            if out_buf is not None:
                out_buf.close()
            if err_buf is not None:
                err_buf.close()
            if script_path:
                try:
                    os.remove(script_path)
                except Exception:
                    pass

        transcript.add("\n--- SUMMARY ---\n" + "\n".join(summary) + "\n")
        truncated = truncated or structured.truncated or transcript.dropped_chars > 0
        return transcript.get(), structured.items, exit_code, truncated

    # -- history -----------------------------------------------------------

    def _record_history(self, run_id, command, working_dir, chained,
                        exit_code, duration_ms, truncated, note=None):
        entry = {
            "run_id": run_id,
            "timestamp": time.time(),
            "command": command,
            "working_dir": working_dir or "",
            "armed": True,
            "chained": bool(chained),
            "mode": "chained" if chained else "lines",
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "truncated": bool(truncated),
        }
        if note:
            entry["note"] = note
        append_history(entry)

    @staticmethod
    def _summary(run_id, mode, exit_code, duration_ms, truncated, overflow_dir=None):
        lines = [
            f"run_id: {run_id}",
            f"mode: {mode}",
            f"exit_code: {exit_code}",
            f"duration_ms: {duration_ms}",
            f"truncated: {'true' if truncated else 'false'}",
        ]
        if overflow_dir is not None:
            lines.append(f"overflow: {overflow_dir}")
        return "\n".join(lines) + "\n"


def _merge_extra_env(env, extra_env):
    """Merge KEY=value lines into the environment, skipping anything that is
    not a valid, non-empty name (an empty name would crash the spawn on
    Windows and produce a useless entry on POSIX)."""
    for raw in (extra_env or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not sep or not key:
            print(f"[RunCommandNode] Ignoring extra_env line (expected KEY=value): {raw!r}")
            continue
        if any(ch.isspace() for ch in key) or "\x00" in key or "\x00" in value:
            print(f"[RunCommandNode] Ignoring extra_env line (invalid variable name): {raw!r}")
            continue
        env[key] = value


# Node class mappings for ComfyUI
NODE_CLASS_MAPPINGS = {
    "RunCommand (DANGEROUS)": RunCommandNode
}

# Optional: A display name mapping
NODE_DISPLAY_NAME_MAPPINGS = {
    "RunCommand (DANGEROUS)": "Run Shell Command (DANGEROUS)"
}
