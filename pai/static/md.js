// Shared text helpers for the owner UI and the guest page. Loaded before app.js / guest.js.
// Safe for text AND for quoted attribute values. innerHTML alone leaves quotes
// alone, which let a value close an attribute (title="...", value="...") and
// inject handlers, so quotes are escaped here too.
function esc(s) {
  const d = document.createElement("div");
  d.textContent = s === undefined || s === null ? "" : String(s);
  return d.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* ===================== tiny markdown renderer (no deps) ===================== */
/* Deliberately minimal: headings, bold/italic, inline code, fenced code
   blocks, lists, blockquotes, links, hr, tables. Escapes HTML first so raw
   markdown text can never inject markup. */
function renderMarkdown(src) {
  if (!src) return "";
  const codeBlocks = [];
  // Code blocks are swapped for placeholders and restored only at the very end,
  // so their contents never pass through the line-based markdown rules below.
  let text = src.replace(/```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const idx = codeBlocks.length;
    codeBlocks.push(`<pre><code>${esc(code.replace(/\n$/, ""))}</code></pre>`);
    return `\u0000CODEBLOCK${idx}\u0000`;
  });

  text = esc(text);

  const lines = text.split("\n");
  const out = [];
  let inUl = false, inOl = false, inQuote = false;
  const closeLists = () => {
    if (inUl) { out.push("</ul>"); inUl = false; }
    if (inOl) { out.push("</ol>"); inOl = false; }
    if (inQuote) { out.push("</blockquote>"); inQuote = false; }
  };
  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/!\[([^\]"<>]{0,200})\]\((https:\/\/upload\.wikimedia\.org\/[^\s"'<>()]+)\)/g, '<img src="$2" alt="$1" loading="lazy" referrerpolicy="no-referrer" style="max-width:100%;border-radius:8px"/>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\(((?:https?:\/\/)[^)\s"'<>]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  for (const raw of lines) {
    if (/^\u0000CODEBLOCK\d+\u0000$/.test(raw)) { closeLists(); out.push(raw); continue; }
    const line = raw;
    if (/^\s*$/.test(line)) { closeLists(); continue; }
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) {
      closeLists();
      const lvl = m[1].length;
      out.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`);
      continue;
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { closeLists(); out.push("<hr/>"); continue; }
    if ((m = line.match(/^&gt;\s?(.*)$/))) {
      if (!inQuote) { closeLists(); out.push("<blockquote>"); inQuote = true; }
      out.push(inline(m[1]) + "<br/>");
      continue;
    }
    if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      if (inOl) { out.push("</ol>"); inOl = false; }
      if (inQuote) { out.push("</blockquote>"); inQuote = false; }
      if (!inUl) { out.push("<ul>"); inUl = true; }
      out.push(`<li>${inline(m[1])}</li>`);
      continue;
    }
    if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
      if (inUl) { out.push("</ul>"); inUl = false; }
      if (inQuote) { out.push("</blockquote>"); inQuote = false; }
      if (!inOl) { out.push("<ol>"); inOl = true; }
      out.push(`<li>${inline(m[1])}</li>`);
      continue;
    }
    closeLists();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeLists();
  return out.join("\n").replace(/\u0000CODEBLOCK(\d+)\u0000/g, (_, i) => codeBlocks[Number(i)]);
}

