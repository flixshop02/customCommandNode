# RunCommand (DANGEROUS) — ComfyUI custom node

A ComfyUI node that runs shell commands on the ComfyUI host when a workflow
is queued. It exists to be useful on a **single-user, local machine** and is
**arbitrary code execution by design** — install it accordingly.

## Install

Copy this folder into `ComfyUI/custom_nodes/` (the folder must contain
`__init__.py`, `command_executor.py`, `routes.py` and `web/js/`), then
restart ComfyUI. Optionally `pip install -r requirements.txt` for reliable
cross-platform process-tree killing (`psutil` is recommended but optional).

## Safety model (read this)

- **Nothing runs unless `armed` is on.** The toggle defaults to off, and the
  bundled frontend extension forces it back to off every time a workflow is
  loaded, so a downloaded workflow cannot silently carry a pre-armed command.
- **That is a guardrail, not a security boundary.** Anything that can queue a
  prompt (the REST API, a script, a client where the extension isn't loaded)
  can pass `armed=true`. Prevent that with `COMFYUI_RUNCOMMAND_DISABLE=1` in
  ComfyUI's environment (hard, server-side off switch), or simply never expose
  the server to untrusted clients.
- Snippet names, run history and overflow output are stored under ComfyUI's
  per-user data directory: `<user dir>/run_command_node/`.

## Node inputs

| Input | Meaning |
| --- | --- |
| `armed` | Master on/off switch. Required for anything to run. |
| `command` | Shell command(s), one per line. Lines starting with `#` are comments (skipped in line mode, native comments in a chained script). |
| `working_dir` | Optional working directory for the commands. |
| `stop_on_error` | Stop at the first failing line (`set -e -o pipefail` in a chained POSIX script; `if errorlevel` guards in a chained `.bat`). |
| `timeout` | Seconds. **Per line** in line mode, **whole script** in chained mode. |
| `structured_output` | Return a JSON array of per-line results instead of the text transcript. |
| `chain_commands` | Run all lines as one shell script instead of one shell per line. |
| `snippet` | UI-only picker (free text server-side). Selecting an entry copies its command/working directory into the node. |
| `extra_env` | `KEY=value` lines merged into the command's environment. |

Outputs: `output` (full transcript, or JSON with `structured_output`) and
`logs` (compact status: run id, mode, exit code, duration, truncation).

## Behaviour notes

- **The node always executes when its workflow is queued** (`OUTPUT_NODE` +
  always-changed `IS_CHANGED`), because it is a side-effect node. ComfyUI's
  node cache is never allowed to skip it.
- Execution is async: the ComfyUI server keeps responding while a command
  runs. If several `RunCommand` nodes are in one prompt they may overlap.
- Timeout and the ComfyUI **Cancel** button kill the whole process tree
  (psutil when available, otherwise POSIX process-group kill / Windows
  `taskkill /T /F`), so backgrounded children do not survive as orphans.
- Output is capped at 512 KB per stream in memory; everything is streamed to
  `overflow/<run_id>.*` on disk, and the oldest overflow files are pruned
  automatically (7 days / 200 files).
- Oversized transcripts and structured output are truncated in the returned
  strings with a pointer to the overflow files; run history rotates at 2 MB
  (keeping the newest 5000 entries). Disarmed/refused runs are *not*
  recorded — only actual executions.
- Live log chunks are also published over ComfyUI's websocket as
  `runcommand.log` events (throttled, available on `node._rcnLiveLog`).

## HTTP endpoints (used by the frontend extension)

- `GET  /run_command_node/snippets`
- `POST /run_command_node/snippets` (JSON body; upserts by name)
- `DELETE /run_command_node/snippets/{id}`
- `GET  /run_command_node/history?n=20` (clamped to 1–200)

## v2 change list (fixes over the previous overhaul)

1. **Frontend actually loads**: the extension now lives at
   `web/js/run_command_ui.js`, matching `WEB_DIRECTORY = "web"` (previously
   the file sat at the package root, so the forced-disarm guard and all UI
   helpers never loaded).
2. **`OUTPUT_NODE = True` + `IS_CHANGED`**: the node used to be skipped when
   its outputs were unconnected, and ComfyUI's cache could reuse a previous
   result on a repeat queue. It now runs every time.
3. **`snippet` is a STRING, not a combo**: a deleted/renamed snippet (or a
   workflow imported from someone else) can no longer fail the whole prompt
   with `Value not in list`. The frontend upgrades the field to a dropdown.
4. **Chained mode no longer deletes `#` lines on POSIX** (heredocs and inline
   scripts survive); on Windows they are rewritten to `REM`. `stop_on_error`
   is now honoured in chained mode.
5. **Bounded memory and disk**: aggregate transcript/structured-output caps,
   history rotation, overflow pruning, and throttled websocket logging.
6. **Non-blocking `taskkill`**: the Windows kill fallback no longer stalls
   ComfyUI's event loop for up to ~5 seconds.
7. **Robustness**: `extra_env` lines with invalid names are skipped with a
   warning; corrupt `snippets.json` is moved aside instead of silently
   overwritten; saving a snippet with an existing name updates it instead of
   creating duplicates; hard task cancellation kills the process tree.
8. **HTTP/UI hygiene**: JSON content type required for snippet POSTs,
   validated/clamped inputs, working snippet delete button, and clearer
   history output. Refusals (disarmed, globally disabled, empty command, bad
   working directory) return a status summary and no longer spam history.
9. **Accurate documentation** of the arm/disarm guardrail (UI-level) and the
   `COMFYUI_RUNCOMMAND_DISABLE=1` hard kill switch.
