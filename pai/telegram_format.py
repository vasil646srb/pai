"""Telegram text formatting: Markdown to Telegram HTML, and length-safe splitting.

Pure functions with no module state. Re-exported from pai.telegram for existing callers."""
from __future__ import annotations

import html as _html
import re


_FENCE_RE = re.compile(r"```[^\n]*\n?(.*?)```", re.S)


_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")


def md_to_tg_html(text: str) -> str:
    """Model Markdown -> Telegram HTML (b, i, s, code, pre, a, blockquote). Telegram shows no Markdown.
    Code is held aside first, so markup inside it is never touched; everything else is HTML-escaped."""
    holds: list[str] = []

    def hold(tag: str, content: str) -> str:
        holds.append(f"<{tag}>{_html.escape(content, quote=False)}</{tag}>")
        return f"\x00{len(holds) - 1}\x00"

    t = _FENCE_RE.sub(lambda m: hold("pre", m.group(1).rstrip("\n")), text or "")
    t = _INLINE_CODE_RE.sub(lambda m: hold("code", m.group(1)), t)

    lines: list[str] = []
    table: list[str] = []

    def flush_table():
        if table:
            lines.append(hold("pre", "\n".join(table)))
            table.clear()

    for raw in t.split("\n"):
        line = raw.rstrip()
        if _TABLE_LINE_RE.match(line):
            table.append(line)
            continue
        flush_table()
        if re.fullmatch(r"\s*(-{3,}|\*{3,}|_{3,})\s*", line):
            lines.append("")
            continue
        m = re.match(r"\s*#{1,6}\s+(.*)", line)
        if m:
            lines.append("**" + m.group(1).strip() + "**")
            continue
        m = re.match(r"\s*[-*+]\s+(.*)", line)
        if m:
            lines.append("• " + m.group(1))
            continue
        m = re.match(r"\s*>\s?(.*)", line)
        if m:
            lines.append("\x01Q" + m.group(1))
            continue
        lines.append(line)
    flush_table()

    out: list[str] = []
    quote: list[str] = []
    for line in lines:
        if line.startswith("\x01Q"):
            quote.append(_html.escape(line[2:], quote=False))
            continue
        if quote:
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote = []
        out.append(line if line.startswith("\x00") else _html.escape(line, quote=False))
    if quote:
        out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
    s = "\n".join(out)

    s = re.sub(r'\[([^\]\n]+)\]\((https?://[^\s)"<>]+)\)', lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: holds[int(m.group(1))], s)
    return s.strip()


def split_text(text: str, limit: int) -> list[str]:
    """Split at paragraph, line, sentence or word boundary so every part is at most `limit` characters."""
    text = (text or "").strip()
    parts: list[str] = []
    while len(text) > limit:
        cut = -1
        for sep in ("\n\n", "\n", ". ", " "):
            i = text.rfind(sep, 0, limit)
            if i >= limit // 2:
                cut = i + (0 if sep == "\n\n" else len(sep.rstrip()))
                break
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text or not parts:
        parts.append(text)
    return parts


def shorten(text: str, limit: int) -> str:
    parts = split_text(text, limit)
    return parts[0] if len(parts) == 1 else parts[0].rstrip(" ,;:") + "…"
