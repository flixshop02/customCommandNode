/**
 * Pure text helper for the RunCommand node's Ctrl+/ (Cmd+/) line-comment
 * toggle. Kept free of imports so it can be unit-tested with plain Node
 * (see tests/comment_toggle.test.mjs).
 */

/**
 * Toggle '#' line comments over every line touched by the selection.
 *
 * - If every non-empty line is already commented, the '#' is removed.
 * - Otherwise, only uncommented lines get a '# ' prefix (indentation kept).
 * - Empty lines are never modified.
 *
 * Returns { value, start, end } with the new text and the selection that
 * should cover the edited block, or null when there is nothing to change.
 */
export function toggleCommentBlock(value, selStart, selEnd) {
    const text = value ?? "";
    const start = Math.max(0, Math.min(selStart ?? 0, text.length));
    const end = Math.max(start, Math.min(selEnd ?? start, text.length));

    // Expand the selection to full lines.
    const before = start > 0 ? text.lastIndexOf("\n", start - 1) : -1;
    const blockStart = before + 1;

    // If the selection ends right after a newline, the next line is only
    // touched when the selection actually reaches into it.
    let blockEnd;
    if (end > blockStart && text[end - 1] === "\n") {
        blockEnd = end - 1;
    } else {
        blockEnd = text.indexOf("\n", end);
        if (blockEnd === -1) {
            blockEnd = text.length;
        }
    }

    const block = text.slice(blockStart, blockEnd);
    const lines = block.split("\n");
    const nonEmpty = lines.filter((line) => line.trim() !== "");
    if (nonEmpty.length === 0) return null;

    const allCommented = nonEmpty.every((line) => line.trimStart().startsWith("#"));
    const updated = lines
        .map((line) => {
            if (line.trim() === "") return line;
            if (allCommented) return line.replace(/^(\s*)#\s?/, "$1");
            if (line.trimStart().startsWith("#")) return line;
            return line.replace(/^(\s*)/, "$1# ");
        })
        .join("\n");
    if (updated === block) return null;

    return {
        value: text.slice(0, blockStart) + updated + text.slice(blockEnd),
        start: blockStart,
        end: blockStart + updated.length,
    };
}
