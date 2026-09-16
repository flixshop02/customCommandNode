import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

// Must match NODE_CLASS_MAPPINGS key in command_executor.py exactly.
const NODE_TYPE = "RunCommand (DANGEROUS)";

const ARMED_COLOR = "#5a1a1a";
const ARMED_BGCOLOR = "#7a2323";
const SNIPPET_NONE = "(none)";

function findWidget(node, name) {
    return node.widgets ? node.widgets.find((w) => w.name === name) : undefined;
}

function markDirty(node) {
    if (typeof node.setDirtyCanvas === "function") {
        node.setDirtyCanvas(true, true);
    }
}

function applyArmedVisuals(node) {
    const armedWidget = findWidget(node, "armed");
    if (!armedWidget) return;
    if (armedWidget.value) {
        node.color = ARMED_COLOR;
        node.bgcolor = ARMED_BGCOLOR;
    } else {
        // Fall back to ComfyUI's default node coloring.
        delete node.color;
        delete node.bgcolor;
    }
    markDirty(node);
}

// --- snippet API helpers -----------------------------------------------

async function fetchSnippets() {
    try {
        const resp = await api.fetchApi("/run_command_node/snippets");
        if (!resp.ok) return null;
        const data = await resp.json();
        return Array.isArray(data) ? data : null;
    } catch (e) {
        console.warn("[RunCommandNode] Failed to fetch snippets", e);
        return null;
    }
}

async function saveSnippetRequest(name, command, workingDir) {
    const resp = await api.fetchApi("/run_command_node/snippets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, command, working_dir: workingDir }),
    });
    if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try {
            const body = await resp.json();
            if (body && body.error) detail = body.error;
        } catch (e) {
            // keep the HTTP status as the message
        }
        throw new Error(detail);
    }
    return await resp.json();
}

async function deleteSnippetRequest(snippetId) {
    const resp = await api.fetchApi(
        `/run_command_node/snippets/${encodeURIComponent(snippetId)}`,
        { method: "DELETE" },
    );
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
}

// --- snippet widget ------------------------------------------------------

function refreshSnippetWidget(node, snippets) {
    if (!snippets) return; // fetch failed — keep whatever the widget already has
    node._rcnSnippets = snippets;
    const widget = findWidget(node, "snippet");
    if (!widget) return;
    const names = [SNIPPET_NONE, ...snippets.map((s) => s.name)];
    if (widget.options) {
        widget.options.values = names;
    }
    // A snippet name that no longer exists (deleted, renamed, or imported
    // from someone else's workflow) falls back to "(none)" — harmless now
    // that the Python side treats this as a plain string.
    if (widget.value && widget.value !== SNIPPET_NONE && !names.includes(widget.value)) {
        widget.value = SNIPPET_NONE;
    }
    markDirty(node);
}

function applySnippet(node, name) {
    if (!name || name === SNIPPET_NONE) return;
    const match = (node._rcnSnippets || []).find((s) => s.name === name);
    if (!match) return;
    const commandWidget = findWidget(node, "command");
    const workingDirWidget = findWidget(node, "working_dir");
    if (commandWidget) commandWidget.value = match.command;
    if (workingDirWidget && match.working_dir) workingDirWidget.value = match.working_dir;
    markDirty(node);
}

// The Python side declares 'snippet' as a plain STRING on purpose (so a stale
// name can never fail server-side prompt validation). Here we upgrade that
// text field into a real combo dropdown, keeping it in exactly the same slot
// in node.widgets so positional widget values in existing workflows still map
// to the same inputs.
function upgradeSnippetWidget(node) {
    const original = findWidget(node, "snippet");
    if (!original || original.type === "combo") return original;
    try {
        const index = node.widgets.indexOf(original);
        const combo = node.addWidget(
            "combo",
            "snippet",
            original.value ?? SNIPPET_NONE,
            (value) => applySnippet(node, value),
            { values: [SNIPPET_NONE] },
        );
        // Move the freshly appended combo into the original widget's slot.
        const comboIndex = node.widgets.indexOf(combo);
        if (comboIndex >= 0) {
            node.widgets.splice(comboIndex, 1);
        }
        if (typeof node.removeWidget === "function") {
            node.removeWidget(original);
        } else {
            const i = node.widgets.indexOf(original);
            if (i >= 0) node.widgets.splice(i, 1);
        }
        node.widgets.splice(Math.min(index, node.widgets.length), 0, combo);
        return combo;
    } catch (e) {
        console.warn(
            "[RunCommandNode] Could not turn 'snippet' into a dropdown; " +
            "leaving it as a text field (everything else still works).",
            e,
        );
        return original;
    }
}

// --- buttons -------------------------------------------------------------

function addSnippetButtons(node) {
    node.addWidget("button", "Save as snippet", null, async () => {
        const commandWidget = findWidget(node, "command");
        const cmd = commandWidget ? commandWidget.value : "";
        if (!cmd || !cmd.trim()) {
            alert("Command is empty — nothing to save.");
            return;
        }
        const name = prompt("Snippet name:");
        if (!name || !name.trim()) return;
        const workingDirWidget = findWidget(node, "working_dir");
        try {
            const snippets = await saveSnippetRequest(
                name.trim(),
                cmd,
                workingDirWidget ? workingDirWidget.value : "",
            );
            refreshSnippetWidget(node, snippets);
            const snippetWidget = findWidget(node, "snippet");
            if (snippetWidget && snippets.some((s) => s.name === name.trim())) {
                snippetWidget.value = name.trim();
            }
            markDirty(node);
        } catch (e) {
            alert(`Failed to save snippet: ${e.message || e}`);
        }
    });

    node.addWidget("button", "Delete snippet", null, async () => {
        const snippetWidget = findWidget(node, "snippet");
        const name = snippetWidget ? snippetWidget.value : SNIPPET_NONE;
        if (!name || name === SNIPPET_NONE) {
            alert("Pick a snippet in the 'snippet' dropdown first.");
            return;
        }
        const match = (node._rcnSnippets || []).find((s) => s.name === name);
        if (!match) {
            alert(`Snippet "${name}" was not found on the server.`);
            return;
        }
        if (!confirm(`Delete snippet "${name}"?`)) return;
        try {
            const snippets = await deleteSnippetRequest(match.id);
            refreshSnippetWidget(node, snippets);
            if (snippetWidget && snippetWidget.value === name) {
                snippetWidget.value = SNIPPET_NONE;
            }
            markDirty(node);
        } catch (e) {
            alert(`Failed to delete snippet: ${e.message || e}`);
        }
    });

    node.addWidget("button", "Show recent history", null, async () => {
        try {
            const resp = await api.fetchApi("/run_command_node/history?n=10");
            const history = await resp.json();
            if (!Array.isArray(history) || !history.length) {
                alert("No run history yet.");
                return;
            }
            const lines = history
                .slice()
                .reverse()
                .map((h) => {
                    const when = new Date((h.timestamp || 0) * 1000).toLocaleString();
                    let status;
                    if (h.note) {
                        status = h.note;
                    } else if (h.exit_code === null || h.exit_code === undefined) {
                        status = "no exit code";
                    } else {
                        status = `exit ${h.exit_code}`;
                    }
                    const mode = h.mode || (h.chained ? "chained" : "lines");
                    return `[${when}] (${status}, ${mode}, ${h.duration_ms ?? "?"}ms)\n${h.command ?? ""}`;
                });
            alert(lines.join("\n\n"));
        } catch (e) {
            alert("Failed to load run history.");
        }
    });
}

// --- extension -----------------------------------------------------------

app.registerExtension({
    name: "RunCommandNode.UI",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_TYPE) return;

        // --- Node instance setup -----------------------------------------
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
            const node = this;

            // Arm/Disarm visuals: red-ish tint while armed, default otherwise.
            applyArmedVisuals(node);
            const armedWidget = findWidget(node, "armed");
            if (armedWidget) {
                const origCallback = armedWidget.callback;
                armedWidget.callback = function (value) {
                    const res = origCallback ? origCallback.apply(this, arguments) : undefined;
                    applyArmedVisuals(node);
                    return res;
                };
            }

            try {
                upgradeSnippetWidget(node);
                addSnippetButtons(node);
                fetchSnippets().then((snippets) => refreshSnippetWidget(node, snippets));
            } catch (e) {
                // Never let UI setup break node creation.
                console.warn("[RunCommandNode] UI setup failed (node still works)", e);
            }

            return r;
        };

        // --- Force 'armed' back to False on every workflow load ----------
        // Core defense against a shared/downloaded workflow silently carrying
        // a pre-armed dangerous command: no matter what the saved JSON says,
        // the node comes back disarmed and you must consciously re-arm it.
        // (This is a guardrail for the UI; the REST API can still pass
        // armed=true — see README.md.)
        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
            const node = this;
            const armedWidget = findWidget(node, "armed");
            if (armedWidget && armedWidget.value) {
                armedWidget.value = false;
                console.log(
                    "[RunCommandNode] Workflow loaded with armed=true — forced back to " +
                        "disarmed for safety. Re-arm manually if you really intend to run this.",
                );
            }
            applyArmedVisuals(node);
            fetchSnippets().then((snippets) => refreshSnippetWidget(node, snippets));
            return r;
        };
    },

    async setup() {
        // Live log streaming: accumulate incoming chunks per-node. No
        // dedicated log panel yet — stored on the node object so a future
        // panel, or the browser console, can inspect node._rcnLiveLog.
        api.addEventListener("runcommand.log", (event) => {
            const detail = event.detail || {};
            const { node: nodeId, text } = detail;
            if (!text) return;
            const node = app.graph.getNodeById(Number(nodeId));
            if (node) {
                node._rcnLiveLog = (node._rcnLiveLog || "") + text;
                if (node._rcnLiveLog.length > 20000) {
                    node._rcnLiveLog = node._rcnLiveLog.slice(-20000);
                }
            }
        });
    },
});
