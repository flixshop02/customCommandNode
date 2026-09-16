// Unit tests for the pure Ctrl+/ comment-toggle helper used by the
// RunCommand node UI. Run with:  node tests/comment_toggle.test.mjs

import assert from "node:assert/strict";
import { toggleCommentBlock } from "../web/js/comment_toggle.js";

function apply(value, start = value.length, end = start) {
    const result = toggleCommentBlock(value, start, end);
    return result ? result.value : value;
}

let passed = 0;
function check(name, fn) {
    fn();
    passed++;
    console.log(`ok - ${name}`);
}

check("comments the current line when there is no selection", () => {
    assert.equal(apply("echo hi", 3), "# echo hi");
});

check("uncomments an already commented line", () => {
    assert.equal(apply("# echo hi", 3), "echo hi");
});

check("preserves indentation when commenting", () => {
    assert.equal(apply("    echo hi", 8), "    # echo hi");
});

check("preserves indentation when uncommenting", () => {
    assert.equal(apply("    # echo hi", 8), "    echo hi");
});

check("expands a partial selection to whole lines", () => {
    assert.equal(apply("echo hi there", 5, 7), "# echo hi there");
});

check("comments every selected line", () => {
    assert.equal(apply("one\ntwo\nthree", 0, 7), "# one\n# two\nthree");
});

check("uncomments every selected line", () => {
    assert.equal(apply("# one\n# two\nthree", 0, 11), "one\ntwo\nthree");
});

check("leaves already commented lines alone in a mixed selection", () => {
    assert.equal(apply("one\n# two", 0, 11), "# one\n# two");
});

check("never modifies blank lines", () => {
    assert.equal(apply("one\n\ntwo", 0, 8), "# one\n\n# two");
});

check("a selection ending after a newline does not include the next line", () => {
    assert.equal(apply("one\ntwo", 0, 4), "# one\ntwo");
});

check("does nothing for empty or whitespace-only blocks", () => {
    assert.equal(toggleCommentBlock("", 0, 0), null);
    assert.equal(toggleCommentBlock("   ", 0, 3), null);
});

check("handles a lone '#' comment marker", () => {
    assert.equal(apply("#", 0, 1), "");
});

check("clamps out-of-range selections", () => {
    assert.equal(apply("abc", -5, 99), "# abc");
});

check("tolerates a reversed selection", () => {
    assert.equal(apply("abc", 3, 0), "# abc");
});

check("returns a selection covering the edited block", () => {
    const result = toggleCommentBlock("one\ntwo", 0, 3);
    assert.equal(result.value, "# one\ntwo");
    assert.equal(result.start, 0);
    assert.equal(result.end, "# one".length);
});

check("toggling twice restores a uniformly uncommented block", () => {
    const original = "  one\n\n  two";
    const once = apply(original, 0, original.length);
    const twice = apply(once, 0, once.length);
    assert.equal(once, "  # one\n\n  # two");
    assert.equal(twice, original);
});

check("mixed blocks: second pass uncomments every line", () => {
    const mixed = "one\n# two";
    const once = apply(mixed, 0, mixed.length);
    assert.equal(once, "# one\n# two");
    const twice = apply(once, 0, once.length);
    assert.equal(twice, "one\ntwo");
});

console.log(`\n${passed} tests passed`);
