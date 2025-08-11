// Enhances the "command" STRING widget with CodeMirror (comment toggle, search, indent, etc.)
;(function () {
  const EXT_NAME = "customCommandNode.RunCommandEditor";
  const CM_VER = "5.65.16";
  const CDN = "https://cdnjs.cloudflare.com/ajax/libs/codemirror/" + CM_VER + "/";

  function loadCSS(href) {
    return new Promise((res, rej) => {
      if ([...document.styleSheets].some(s => s.href && s.href.includes(href))) return res();
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      link.onload = () => res();
      link.onerror = rej;
      document.head.appendChild(link);
    });
  }
  function loadScript(src) {
    return new Promise((res, rej) => {
      if (document.querySelector(`script[data-src="${src}"]`)) return res();
      const s = document.createElement("script");
      s.dataset.src = src;
      s.src = src;
      s.onload = () => res();
      s.onerror = rej;
      document.head.appendChild(s);
    });
  }

  let cmReady;
  function ensureCodeMirror() {
    if (!cmReady) {
      cmReady = (async () => {
        await loadCSS(CDN + "codemirror.min.css");
        await loadCSS(CDN + "addon/dialog/dialog.min.css");
        await loadScript(CDN + "codemirror.min.js");
        await Promise.all([
          loadScript(CDN + "mode/shell/shell.min.js"),
          loadScript(CDN + "addon/comment/comment.min.js"),
          loadScript(CDN + "addon/search/search.min.js"),
          loadScript(CDN + "addon/search/searchcursor.min.js"),
          loadScript(CDN + "addon/edit/matchbrackets.min.js"),
        ]);
      })();
    }
    return cmReady;
  }

  function enhanceNode(node) {
    try {
      const w = node.widgets?.find(w => w.name === "command" && w.inputEl && !w._cmEnhanced);
      if (!w) return;

      const textarea = w.inputEl;
      const host = document.createElement("div");
      host.style.height = "220px";
      host.style.border = "1px solid var(--input-border-color, #444)";
      host.style.borderRadius = "4px";
      host.style.overflow = "hidden";
      textarea.style.display = "none";
      textarea.parentElement.insertBefore(host, textarea);

      ensureCodeMirror().then(() => {
        const cm = window.CodeMirror(host, {
          value: w.value || textarea.value || "",
          mode: "shell",
          lineNumbers: true,
          lineWrapping: true,
          matchBrackets: true,
          indentUnit: 2,
          tabSize: 2,
          extraKeys: {
            "Ctrl-/": "toggleComment",
            "Cmd-/": "toggleComment",
            "Tab": cm => { if (cm.somethingSelected()) cm.indentSelection("add"); else cm.replaceSelection("  ", "end"); },
            "Shift-Tab": cm => cm.indentSelection("subtract"),
            "Ctrl-F": "findPersistent",
            "Cmd-F": "findPersistent",
            "Ctrl-G": "findNext",
            "Shift-Ctrl-G": "findPrev",
          },
        });

        const sync = () => { w.value = cm.getValue(); };
        cm.on("change", sync);
        sync();

        // Report size to LiteGraph
        w.computeSize = w.computeSize || function () { return [node.size[0] - 20, 230]; };

        // Ensure serialization uses editor content and restores properly
        w.serializeValue = () => cm.getValue();
        w.setValue = (v) => { if (typeof v === "string") cm.setValue(v); };

        const origResize = node.onResize;
        node.onResize = function () {
          const r = origResize ? origResize.apply(this, arguments) : undefined;
          cm.refresh();
          return r;
        };

        w._cmEnhanced = true;
      }).catch(err => {
        console.warn(EXT_NAME, "CodeMirror load failed", err);
        textarea.style.display = "";
        host.remove();
      });
    } catch (e) {
      console.warn(EXT_NAME, "enhance error", e);
    }
  }

  app.registerExtension({
    name: EXT_NAME,
    nodeCreated(node) {
      if (node?.comfyClass === "RunCommand (DANGEROUS)") {
        setTimeout(() => enhanceNode(node), 0);
      }
    },
  });
})();