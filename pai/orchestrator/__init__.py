"""Orchestrator package: dialog loop decomposed into focused modules.

Public API:
    Orchestrator — facade holding all components
    TurnResult — result of a single turn
    MAX_TOOL_ITERATIONS — default tool iteration limit
    _ITERATION_LIMIT_FALLBACK — message when iterations exhausted
"""

from .base import (
    _ITERATION_LIMIT_FALLBACK,
    MAX_TOOL_ITERATIONS,
    Orchestrator,
    TurnResult,
    _build_user_content,
)
from .dialog import DialogLoop

__all__ = [
    "Orchestrator",
    "TurnResult",
    "DialogLoop",
    "_build_user_content",
    "MAX_TOOL_ITERATIONS",
    "_ITERATION_LIMIT_FALLBACK",
]
