"""LLM settings endpoints."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from ...api_schemas import (
    LLMSettingsUpdate,
)
from ...llm import LLMError, MockLLM, OpenAICompatibleClient
from ...llm_config import LLMConfig
from ..builders import (
    build_llm_client,
    build_summarizer_llm_client,
    build_planner_llm_client,
    build_verifier_llm_client,
    build_vision_llm_client,
)


def register(app: FastAPI) -> None:
    _orch0 = getattr(app.state, "orchestrator", None)
    if _orch0 is not None:  # planner model is applied from the saved config at startup too
        _orch0.planner_llm = build_planner_llm_client(app.state.llm_config_store.load())
    from ...telegram import TelegramError, get_services, init_services

    init_services(app.state.secret_store, app.state.llm_config_store.path.parent, storage=app.state.storage)

    from ...telegram import (send_manual, set_classifier, set_media_classifier, set_options_source,
                             set_owner_responder, set_responder)
    from ...telegram_agent import (GROUP_SPACE, classify, classify_media, group_persona, make_owner_responder,
                                   make_responder)

    app.state.personas.ephemeral(group_persona())  # isolated persona for group replies, never saved to personas.json
    set_classifier(classify)  # Jev: does a group message need an answer?
    set_media_classifier(classify_media)  # Jev: is the owner's voice/video a command or only a note?
    set_responder(make_responder(app.state.orchestrator, app.state.personas))  # persona telegram_group writes the answer
    set_owner_responder(make_owner_responder(app.state.orchestrator))  # owner's private messages = brain commands

    from ...telegram import set_action_executor
    from ...tools.dispatcher import ToolDispatcher

    def _run_confirmed(tool: str, arguments: dict) -> dict:
        """Runs a tool call the owner confirmed in Telegram. Only CONFIRMABLE_TOOLS reach here."""
        orch = app.state.orchestrator
        disp = ToolDispatcher(orch.storage, custom_functions=orch.custom_functions, llm=orch.llm,
                              rules_manager=orch.rules, skills=orch.skills, persona_registry=orch.personas)
        return disp.dispatch(tool, arguments, persona=None)

    set_action_executor(_run_confirmed)  # owner-confirmed bot actions

    def _tg_options() -> dict:
        """What the Telegram page offers: group-safe tools (+ active custom functions) and memory spaces."""
        from ...telegram import GROUP_TOOL_OPTIONS
        custom = []
        cf = getattr(app.state, "custom_functions", None)
        if cf is not None:
            custom = [f.name for f in cf.active()]
        spaces = {"personal", GROUP_SPACE}
        spaces.update(app.state.storage.stats()["by_space"].keys())
        for p in app.state.personas.all():
            spaces.update(p.memory_filter.allow_spaces)
        return {"tools": list(GROUP_TOOL_OPTIONS) + [c for c in custom if c not in GROUP_TOOL_OPTIONS],
                "spaces": sorted(s for s in spaces if s)}

    set_options_source(_tg_options)

    def _tg(fn):
        try:
            return fn()
        except TelegramError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/telegram/settings")
    def tg_settings_get():
        return get_services().public_view()

    @app.put("/telegram/settings")
    def tg_settings_put(body: dict):
        def run():
            s = get_services()
            if body.get("token"):
                s.set_token(str(body["token"]))
            if isinstance(body.get("channels"), list):
                s.save_channels(body["channels"])
            if "enabled" in body:
                s.set_enabled(bool(body["enabled"]))
            if "group_auto_reply" in body:
                s.set_auto_reply(bool(body["group_auto_reply"]))
            if isinstance(body.get("policy"), dict):
                s.save_policy(body["policy"])
            return s.public_view()
        return _tg(run)

    @app.post("/telegram/check")
    def tg_check():
        return _tg(lambda: get_services().check())

    @app.get("/telegram/queue")
    def tg_queue():
        return get_services().queue_view()

    @app.post("/telegram/queue/{item_id}/{action}")
    def tg_queue_act(item_id: str, action: str):
        if action not in ("approve", "reject"):
            raise HTTPException(404, "неизвестное действие")
        return _tg(lambda: get_services()._act(item_id, approve=(action == "approve")))

    @app.post("/telegram/send")
    def tg_send(body: dict):
        return _tg(lambda: send_manual(str(body.get("channel", "")), str(body.get("text", ""))))

    @app.post("/telegram/process")
    def tg_process():
        return _tg(lambda: get_services().process_group())

    @app.post("/telegram/process_owner")
    def tg_process_owner():
        return _tg(lambda: get_services().process_owner())

    @app.get("/telegram/options")
    def tg_options_get():
        from ...telegram import tg_options
        return tg_options()

    @app.get("/telegram/inbox")
    def tg_inbox():
        return get_services().inbox_view()

    @app.post("/telegram/poll")
    def tg_poll():
        return _tg(lambda: get_services().poll())

    @app.get("/settings/llm")
    def get_llm_settings():
        config = app.state.llm_config_store.load()
        return {
            **config.masked(),
            "effective_summarizer_model": config.effective_summarizer_model(),
            "effective_verify_model": config.effective_verify_model(),
            "effective_vision_model": config.effective_vision_model(),
            "active": isinstance(app.state.llm, OpenAICompatibleClient),
            "using_mock": isinstance(app.state.llm, MockLLM),
            "summarizer_active": isinstance(app.state.summarizer_llm, OpenAICompatibleClient),
        }

    @app.put("/settings/llm")
    def put_llm_settings(payload: LLMSettingsUpdate):
        current = app.state.llm_config_store.load()
        new_config = LLMConfig(
            base_url=(payload.base_url if payload.base_url is not None else current.base_url).strip(),
            api_key=(payload.api_key if payload.api_key else current.api_key),
            model=(payload.model if payload.model is not None else current.model).strip(),
            summarizer_model=(payload.summarizer_model if payload.summarizer_model is not None else current.summarizer_model).strip(),
            planner_model=(payload.planner_model if payload.planner_model is not None else current.planner_model).strip(),
            verify_model=(payload.verify_model if payload.verify_model is not None else current.verify_model).strip(),
            vision_model=(payload.vision_model if payload.vision_model is not None else current.vision_model).strip(),
            verify_strictness=(payload.verify_strictness if payload.verify_strictness is not None else current.verify_strictness),
            verify_max_checks=(payload.verify_max_checks if payload.verify_max_checks is not None else current.verify_max_checks),
            site_url=(payload.site_url if payload.site_url is not None else current.site_url).strip(),
            site_name=(payload.site_name if payload.site_name is not None else current.site_name).strip(),
            temperature=(payload.temperature if payload.temperature is not None else current.temperature),
            max_tokens=(payload.max_tokens if payload.max_tokens is not None else current.max_tokens),
            top_p=(None if payload.clear_top_p else (payload.top_p if payload.top_p is not None else current.top_p)),
            reasoning_enabled=(
                payload.reasoning_enabled if payload.reasoning_enabled is not None else current.reasoning_enabled
            ),
            reasoning_effort=(payload.reasoning_effort or current.reasoning_effort),
            context_window=(payload.context_window if payload.context_window is not None else current.context_window),
            max_tool_iterations=(payload.max_tool_iterations if payload.max_tool_iterations is not None else current.max_tool_iterations),
            top_k=(None if payload.clear_top_k else (payload.top_k if payload.top_k is not None else current.top_k)),
            frequency_penalty=(None if payload.clear_frequency_penalty else (payload.frequency_penalty if payload.frequency_penalty is not None else current.frequency_penalty)),
            presence_penalty=(None if payload.clear_presence_penalty else (payload.presence_penalty if payload.presence_penalty is not None else current.presence_penalty)),
            compression_trigger_pct=(payload.compression_trigger_pct if payload.compression_trigger_pct is not None else current.compression_trigger_pct),
        )
        try:
            app.state.llm_config_store.save(new_config)
        except OSError as e:
            raise HTTPException(500, f"Failed to save settings: {e}") from e
        new_llm = build_llm_client(new_config)
        new_summarizer_llm = build_summarizer_llm_client(new_config)
        new_verifier_llm = build_verifier_llm_client(new_config)
        new_planner_llm = build_planner_llm_client(new_config)
        new_vision_llm = build_vision_llm_client(new_config)
        app.state.llm = new_llm
        app.state.summarizer_llm = new_summarizer_llm
        app.state.verifier_llm = new_verifier_llm
        app.state.vision_llm = new_vision_llm
        # The Settings value is authoritative for runtime context management.
        # Provider metadata is still exposed separately by the model/usage code,
        # but must not silently override the user's configured limit.
        known_ctx_len = new_config.context_window if new_config.is_configured() else None
        app.state.orchestrator.reconfigure_llm(
            new_llm, llm_base_url=new_config.base_url or None, known_context_length=known_ctx_len,
            max_tool_iterations=new_config.max_tool_iterations,
            summarizer_llm=new_summarizer_llm,
            verifier_llm=new_verifier_llm,
            verify_strictness=new_config.verify_strictness,
            verify_max_checks=new_config.verify_max_checks,
            vision_llm=new_vision_llm,
            target_free_ratio=new_config.target_free_ratio(),
        )
        app.state.orchestrator.planner_llm = new_planner_llm
        return {
            **new_config.masked(),
            "effective_summarizer_model": new_config.effective_summarizer_model(),
            "effective_verify_model": new_config.effective_verify_model(),
            "effective_planner_model": new_config.effective_planner_model(),
            "effective_vision_model": new_config.effective_vision_model(),
            "active": isinstance(new_llm, OpenAICompatibleClient),
            "using_mock": isinstance(new_llm, MockLLM),
            "summarizer_active": isinstance(new_summarizer_llm, OpenAICompatibleClient),
        }

    @app.get("/settings/llm/models")
    def list_llm_models(base_url: str | None = None, api_key: str | None = None):
        current = app.state.llm_config_store.load()
        effective_base_url = (base_url or current.base_url).strip()
        effective_api_key = current.api_key if api_key is None else api_key
        if not effective_base_url:
            raise HTTPException(400, "Please provide the provider base URL")
        probe = OpenAICompatibleClient(base_url=effective_base_url, api_key=effective_api_key, model="")
        try:
            models = probe.list_models()
        except LLMError as e:
            raise HTTPException(502, f"Failed to fetch the list of models: {e}") from e
        return {"models": models}
