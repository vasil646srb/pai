"""Model-facing prompt texts, kept as plain Markdown files next to this module.

Edit a file to change what the model is told; `load_prompt(name)` reads `<name>.md`.
Some prompts are read once at import time (module-level constants), so restart the
server after editing them.
Names with a slash address subfolders, e.g. "addons/brain".
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent


@cache
def load_prompt(name: str) -> str:
    # the writer adds one trailing newline; the prompt text itself has none
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").removesuffix("\n")
