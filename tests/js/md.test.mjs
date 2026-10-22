// Run with: node --test tests/js/
// Loads the real pai/static/md.js into a VM context with a minimal DOM stub.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

// Browser innerHTML serialises text nodes by escaping &, < and > only (not quotes).
const escapeText = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const document = {
  createElement() {
    let text = "";
    return { set textContent(v) { text = v; }, get innerHTML() { return escapeText(text); } };
  },
};

const here = path.dirname(fileURLToPath(import.meta.url));
const source = readFileSync(path.join(here, "../../pai/static/md.js"), "utf8");
const ctx = vm.createContext({ document });
vm.runInContext(source, ctx);
const { renderMarkdown, esc } = ctx;

test("esc() escapes double and single quotes for attribute contexts", () => {
  const out = esc(`" onmouseover="alert(1)' x='y`);
  assert.ok(!out.includes('"'), `raw double quote leaked: ${out}`);
  assert.ok(!out.includes("'"), `raw single quote leaked: ${out}`);
});

test("esc() still renders ordinary text unchanged", () => {
  assert.equal(esc("Привет & <b>"), "Привет &amp; &lt;b&gt;");
});

test("raw HTML from model output is shown as text, not markup", () => {
  const out = renderMarkdown("<img src=x onerror=alert(1)>");
  assert.ok(out.includes("&lt;img"), out);
  assert.ok(!out.includes("<img"), out);
});

test("code block contents are not reformatted as markdown", () => {
  const out = renderMarkdown("```\n**bold** [l](https://x.org)\n```");
  assert.ok(out.includes("<pre><code>**bold** [l](https://x.org)</code></pre>"), out);
  assert.ok(!out.includes("<strong>"), out);
  assert.ok(!out.includes("<a "), out);
});

test("code block in the middle of a reply keeps its lines intact", () => {
  const out = renderMarkdown("before\n```js\nlet a = 1;\n- item\n```\nafter");
  assert.ok(out.includes("<pre><code>let a = 1;\n- item</code></pre>"), out);
  assert.ok(!out.includes("<li>"), out);
  assert.ok(!out.includes("<p>let a"), out);
});

test("a value built with esc() cannot close its attribute", () => {
  const payload = '" onmouseover="alert(1)" x="';
  const html = `<span title="${esc(payload)}">t</span>`;
  // Exactly the two quotes of title="..." may appear; an injected attribute would add more.
  assert.equal((html.match(/"/g) || []).length, 2, html);
});
