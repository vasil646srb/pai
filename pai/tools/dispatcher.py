"""ToolDispatcher: entry point that routes tool calls."""

from __future__ import annotations

import logging

from .errors import ToolError
from .memory import MemoryToolsMixin
from .persona import PersonaToolsMixin
from .proposals import ProposalToolsMixin
from .schemas import BRAIN_ONLY_TOOLS, BRAIN_PERSONA_ID, TOOL_SCHEMAS
from .sources import SourcesToolsMixin
from .shell import ShellToolsMixin

# Personas that may never change rules or functions, even though propose_* tools are otherwise always granted.
RESTRICTED_PERSONA_PREFIX = "guest"  # 'guest' and every per-room 'guest-room-<id>' persona


def _can_self_modify(persona) -> bool:
    return not str(getattr(persona, "persona_id", "")).startswith(RESTRICTED_PERSONA_PREFIX)
from .web import WebToolsMixin

_log = logging.getLogger(__name__)


class ToolDispatcher(PersonaToolsMixin, ProposalToolsMixin, MemoryToolsMixin, WebToolsMixin, SourcesToolsMixin, ShellToolsMixin):
    """Routes tool calls to the mixins that implement them."""

    def __init__(
        self,
        storage,
        reference_now=None,
        custom_functions=None,
        llm=None,
        rules_manager=None,
        skills=None,
        persona_registry=None,
    ):
        self.skills = skills
        self.persona_registry = persona_registry
        self.storage = storage
        self.reference_now = reference_now
        self.custom_functions = custom_functions
        self.llm = llm
        self.rules_manager = rules_manager

    def schemas_for_persona(self, persona):
        # Built-in tools are granted only according to the persona's permissions.
        is_brain = getattr(persona, "persona_id", None) == BRAIN_PERSONA_ID
        builtins = [
            s
            for s in TOOL_SCHEMAS
            if (s["function"]["name"] in BRAIN_ONLY_TOOLS and is_brain and self.persona_registry is not None)
            or (s["function"]["name"] not in BRAIN_ONLY_TOOLS and s["function"]["name"] in persona.tool_permissions)
            or (s["function"]["name"] == "propose_function" and self.custom_functions is not None
                and _can_self_modify(persona))
            or (s["function"]["name"] == "propose_rule" and self.rules_manager is not None
                and _can_self_modify(persona))
            # get_skill is granted automatically to personas that have skills
            or (
                s["function"]["name"] == "get_skill"
                and self.skills is not None
                and bool(getattr(persona, "active_skill_ids", None))
            )
        ]
        # Custom functions are also persona tools: an active function must be
        # explicitly selected in tool_permissions.
        custom = []
        if self.custom_functions:
            allowed = set(persona.tool_permissions)
            custom = [s for s in self.custom_functions.schemas() if s["function"]["name"] in allowed]
        return builtins + custom

    def _tool_telegram_post(self, arguments, persona):
        from ..telegram import TelegramError, post_from_tool
        try:
            return post_from_tool(str(arguments.get("channel", "")), str(arguments.get("text", "")))
        except TelegramError as e:
            return {"tool_failed": True, "error": str(e), "tool": "telegram_post"}

    def _tool_ask_confirmation(self, arguments, persona):
        from ..telegram import TelegramError, request_from_tool
        try:
            return request_from_tool(str(arguments.get("question", "")), str(arguments.get("tool", "")),
                                     arguments.get("arguments") or {})
        except TelegramError as e:
            return {"tool_failed": True, "tool": "ask_confirmation",
                    "error": f"подтверждение в Telegram недоступно: {e}. Спроси подтверждение прямо в этом чате и жди ответа."}

    def _tool_send_file_to_owner(self, arguments, persona):
        from ..telegram import TelegramError, send_file_from_tool
        try:
            return send_file_from_tool(
                str(arguments.get("kind", "")), url=str(arguments.get("url", "") or ""),
                upload_id=str(arguments.get("upload_id", "") or ""), caption=str(arguments.get("caption", "") or ""))
        except TelegramError as e:
            return {"tool_failed": True, "error": str(e), "tool": "send_file_to_owner"}

    def _tool_telegram_read(self, arguments, persona):
        from ..telegram import TelegramError, read_from_tool
        try:
            return read_from_tool(str(arguments.get("channel", "all") or "all"))
        except TelegramError as e:
            return {"tool_failed": True, "error": str(e), "tool": "telegram_read"}

    def dispatch(self, name, arguments, persona=None):
        if persona is not None and not _can_self_modify(persona) and name in ("propose_function", "propose_rule"):
            raise ToolError("Эта персона не меняет правила и функции")
        if (
            persona is not None
            and name not in persona.tool_permissions
            and name not in ("propose_function", "propose_rule", "get_skill", *BRAIN_ONLY_TOOLS)
        ):
            raise ToolError(f"Persona not permitted to use tool '{name}'")
        handler = getattr(self, f"_tool_{name}", None)
        if handler is not None:
            try:
                return handler(arguments, persona)
            except ToolError as e:
                return {"tool_failed": True, "error": str(e), "tool": name}
            except Exception as e:  # unexpected failure: report to the model, keep the traceback in the log
                _log.exception("tool %s failed", name)
                return {"tool_failed": True, "error": str(e), "tool": name}
        if self.custom_functions and self.custom_functions.get_by_name(name):
            try:
                return self.custom_functions.execute(name, arguments, self.llm)
            except (ToolError, RuntimeError, ValueError) as e:  # user-function errors are expected
                return {"tool_failed": True, "error": str(e), "tool": name}
            except Exception as e:
                _log.exception("custom function %s failed", name)
                return {"tool_failed": True, "error": str(e), "tool": name}
        return {"tool_failed": True, "error": f"Unknown tool: {name}", "tool": name}
