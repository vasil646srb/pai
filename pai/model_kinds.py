"""Which catalog models suit a given job.

Each model is judged by the metadata the provider reports for it. A model with no
metadata is judged by its name instead. The UI can always turn the narrowing off.

  vision: image on input, text output (image generators are not chat vision models)
  tts:    speech synthesis only: output is audio alone (chat models with voice output
          produce text too, so they are not TTS models)
"""
from __future__ import annotations

NAME_HINTS = {
    "vision": ("vision", "-vl", "vl-", "llava", "pixtral", "gpt-4o", "gpt-4.1", "gemini", "claude", "gemma-3"),
    "tts": ("tts", "speech"),
}


def _is_vision(inp: list[str] | None, out: list[str] | None) -> bool:
    return bool(inp) and "image" in inp and (out is None or "text" in out)


def _is_tts(inp: list[str] | None, out: list[str] | None) -> bool:
    return bool(out) and set(out) == {"audio"}


MATCHERS = {"vision": _is_vision, "tts": _is_tts}
KINDS = tuple(MATCHERS)


def filter_models(models: list[dict], kind: str) -> tuple[list[dict], str]:
    """Return (models suitable for kind, mode). mode: 'modalities' if the provider
    reported metadata for at least one model, else 'name'."""
    match = MATCHERS[kind]
    has_meta = any(m.get("input_modalities") is not None or m.get("output_modalities") is not None
                   for m in models)
    rows = []
    for m in models:
        inp, out = m.get("input_modalities"), m.get("output_modalities")
        if inp is not None or out is not None:
            ok = match(inp, out)
        else:
            ok = any(h in m["id"].lower() for h in NAME_HINTS[kind])
        if ok:
            rows.append(m)
    return rows, ("modalities" if has_meta else "name")
