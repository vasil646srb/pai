#!/usr/bin/env python3
"""Adds Laya rows to the 'Лог действий' page (pai/static/app.js, index.html, style.css).
Run from the project root AFTER apply_laya.py. Anchor-based, all-or-nothing per file, writes <file>.pre-laya-ui.
Re-run safe: already patched files are skipped; files whose anchors changed are left untouched."""
import sys
from pathlib import Path

EDITS = {'pai/static/app.js': [('alLabel laya branch',
                        'function alLabel(row, req) {\n  if (row.kind !== "llm_request") return',
                        'function alLabel(row, req) {\n'
                        '  if (row.kind === "laya") return alLayaLabel(row);\n'
                        '  if (row.kind !== "llm_request") return'),
                       ('kind badge',
                        'const kindBadge = row.kind === "llm_request"\n',
                        'const kindBadge = row.kind === "laya" ? `<span class="badge-status laya">laya</span>` : '
                        'row.kind === "llm_request"\n'),
                       ('alBody laya + helpers',
                        'function alBody(row) {\n  const req = alParse(row.request) || {};\n',
                        'function alLayaLabel(row) {\n'
                        '  const lay = (alParse(row.response) || {}).laya || {};\n'
                        '  const parts = Object.entries(lay).map(([q, v]) => `${esc(q)} = ${esc(v.label || "?")} '
                        '(${Math.round((v.p || 0) * 100)}%)`);\n'
                        '  return `laya · <span class="mono">${esc(row.name || "")}</span>${parts.length ? " · " + '
                        'parts.join(", ") : ""}`;\n'
                        '}\n'
                        '\n'
                        'function alLayaBody(row) {\n'
                        '  const req = alParse(row.request) || {};\n'
                        '  const resp = alParse(row.response) || {};\n'
                        '  const lay = resp.laya || {};\n'
                        '  let html = `<div class="al-sec">Запрос</div><pre class="mono '
                        'al-pre">${esc(JSON.stringify(req, null, 2))}</pre>`;\n'
                        '  html += `<div class="al-sec">Ответ Laya</div>`;\n'
                        '  for (const q of Object.keys(lay)) {\n'
                        '    const v = lay[q] || {};\n'
                        '    const probs = Object.entries(v.probs || {}).sort((a, b) => b[1] - a[1])\n'
                        '      .map(([k, p]) => `${esc(k)}: ${(p * 100).toFixed(1)}%`).join(" · ");\n'
                        '    html += `<div class="al-note">${esc(q)} → <b>${esc(v.label || "—")}</b> · отступ '
                        '${esc(String(v.margin ?? "—"))} · confidence ${esc(String(v.confidence ?? "—"))}</div>`;\n'
                        '    html += `<div class="mono al-note">${probs}</div>`;\n'
                        '  }\n'
                        '  if (row.latency_seconds != null) html += `<div class="al-note">задержка '
                        '${Number(row.latency_seconds).toFixed(3)} с</div>`;\n'
                        '  if (resp.actual) html += `<div class="al-sec">Факт (старый путь)</div><pre class="mono '
                        'al-pre">${esc(JSON.stringify(resp.actual, null, 2))}</pre>`;\n'
                        '  if (row.error) html += `<div class="al-sec">ошибка</div><pre class="mono '
                        'al-pre">${esc(row.error)}</pre>`;\n'
                        '  return html;\n'
                        '}\n'
                        '\n'
                        'function alBody(row) {\n'
                        '  if (row.kind === "laya") return alLayaBody(row);\n'
                        '  const req = alParse(row.request) || {};\n')],
 'pai/static/index.html': [('kind filter option',
                            '          <select id="actionLogKind">\n'
                            '            <option value="">все</option>\n'
                            '            <option value="llm_request">llm_request</option>\n'
                            '            <option value="tool_call">tool_call</option>\n'
                            '          </select>',
                            '          <select id="actionLogKind">\n'
                            '            <option value="">все</option>\n'
                            '            <option value="llm_request">llm_request</option>\n'
                            '            <option value="tool_call">tool_call</option>\n'
                            '            <option value="laya">laya</option>\n'
                            '          </select>')],
 'pai/static/style.css': [('laya badge',
                           '.badge-status.comp { background:rgba(180,120,255,.18); color:#b98cff; }',
                           '.badge-status.comp { background:rgba(180,120,255,.18); color:#b98cff; }\n'
                           '.badge-status.laya { background:rgba(127,209,160,.16); color:#7fd1a0; }')]}
MARK = {'pai/static/app.js': 'alLayaBody',
 'pai/static/index.html': 'value="laya"',
 'pai/static/style.css': 'badge-status.laya'}

def main(root="."):
    root = Path(root); bad = 0
    for rel, ed in EDITS.items():
        f = root / rel
        if not f.is_file():
            print(f"SKIP  {rel}: no such file"); bad += 1; continue
        s = f.read_text(encoding="utf-8")
        if MARK[rel] in s:
            print(f"OK    {rel}: already patched"); continue
        miss = [name for name, old, new in ed if s.count(old) != 1]
        if miss:
            print(f"FAIL  {rel}: anchors not found exactly once: {miss} (file untouched)"); bad += 1; continue
        f.with_name(f.name + ".pre-laya-ui").write_text(s, encoding="utf-8")
        for name, old, new in ed:
            s = s.replace(old, new, 1)
        f.write_text(s, encoding="utf-8"); print(f"PATCH {rel}: {len(ed)} edits")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
