import { app } from "../../scripts/app.js";

const CDN_VER = "5.65.16";
const CSS_CORE = `https://cdnjs.cloudflare.com/ajax/libs/codemirror/${CDN_VER}/codemirror.min.css`;
const CSS_THEME = `https://cdnjs.cloudflare.com/ajax/libs/codemirror/${CDN_VER}/theme/monokai.min.css`;
const JS_CORE = `https://cdnjs.cloudflare.com/ajax/libs/codemirror/${CDN_VER}/codemirror.min.js`;
const JS_MODE_SHELL = `https://cdnjs.cloudflare.com/ajax/libs/codemirror/${CDN_VER}/mode/shell/shell.min.js`;

function ensureCss(href) {
	if (document.querySelector(`link[rel="stylesheet"][href="${href}"]`)) return;
	const link = document.createElement("link");
	link.rel = "stylesheet";
	link.href = href;
	document.head.appendChild(link);
}

function ensureScript(src) {
	return new Promise((resolve, reject) => {
		if (document.querySelector(`script[src="${src}"]`)) return resolve();
		const s = document.createElement("script");
		s.src = src;
		s.onload = () => resolve();
		s.onerror = (e) => reject(e);
		document.head.appendChild(s);
	});
}

async function loadCodeMirror() {
	ensureCss(CSS_CORE);
	ensureCss(CSS_THEME);
	await ensureScript(JS_CORE);
	await ensureScript(JS_MODE_SHELL);
	if (!window.CodeMirror) throw new Error("CodeMirror failed to load");
}

function attachCodeMirror(node, widget) {
	if (!widget || widget._cm) return;
	const textarea = widget.inputEl;
	if (!textarea) return;

	// Wait until attached to DOM
	if (!textarea.isConnected) {
		setTimeout(() => attachCodeMirror(node, widget), 0);
		return;
	}

	const cm = window.CodeMirror.fromTextArea(textarea, {
		mode: "shell",
		theme: "monokai",
		lineNumbers: true,
		lineWrapping: true,
		matchBrackets: true,
		indentUnit: 2,
		tabSize: 2,
	});
	widget._cm = cm;

	// Size and node canvas layout
	cm.setSize("100%", 260);
	if (typeof widget.computeSize === "function") {
		const orig = widget.computeSize.bind(widget);
		widget.computeSize = (w) => {
			const size = orig(w);
			size[1] = Math.max(size[1], 280);
			return size;
		};
	}
	node?.graph?.setDirtyCanvas?.(true, true);

	// Sync value back to widget
	cm.on("change", () => {
		const val = cm.getValue();
		widget.value = val;
		if (widget.onChange) widget.onChange(val);
		node?.graph?.setDirtyCanvas?.(true, true);
	});

	// Cleanup on node removal
	const oldOnRemoved = node.onRemoved;
	node.onRemoved = function () {
		try {
			if (widget._cm) {
				widget._cm.toTextArea();
				widget._cm = null;
			}
		} catch {}
		if (oldOnRemoved) return oldOnRemoved.apply(this, arguments);
	};
}

app.registerExtension({
	name: "customCommandNode.CodeMirrorCommandEditor",
	async setup() {
		await loadCodeMirror();
	},
	nodeCreated(node) {
		// Only enhance the "RunCommand (DANGEROUS)" node
		if (node?.comfyClass !== "RunCommand (DANGEROUS)") return;
		const w = node.widgets?.find((x) => x.name === "command");
		if (!w) return;

		const tryAttach = () => {
			if (!w._cm && w.inputEl) attachCodeMirror(node, w);
		};
		tryAttach();

		// Observe DOM in case the widget gets attached later
		const obs = new MutationObserver(() => {
			if (!w._cm && w.inputEl && w.inputEl.isConnected) {
				tryAttach();
			}
		});
		obs.observe(document.body, { childList: true, subtree: true });

		// Stop observing when node is removed
		const prevOnRemoved = node.onRemoved;
		node.onRemoved = function () {
			try { obs.disconnect(); } catch {}
			if (prevOnRemoved) return prevOnRemoved.apply(this, arguments);
		};
	},
});
