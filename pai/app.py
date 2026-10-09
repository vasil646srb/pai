"""Minimal web UI (JSON API) for the personal AI assistant core."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json as _json

from fastapi import Body, Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"


class NoCacheStaticFiles(StaticFiles):
    """Serve static assets directly on every request.

    Browser/proxy caching is intentionally disabled for this development UI.
    We also bypass Starlette's conditional ETag/Last-Modified handling so a
    request cannot be answered with HTTP 304 Not Modified. This guarantees
    that the browser receives the current file bytes from the server.
    """

    def file_response(self, full_path, stat_result, scope, status_code=200):
        from starlette.responses import FileResponse

        response = FileResponse(
            full_path,
            status_code=status_code,
            stat_result=stat_result,
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

from .api_schemas import (
    ChatRequest, ChatResponse, CompressRequest, LLMSettingsUpdate, NodeOut, NodeStatusUpdate,
    BudgetProfileUpdate, PersonaCreate, PersonaOut, PersonaToolsUpdate, PersonaUpdate, RuleCreate, RuleOut,
    SearchRequest, TimeDeltaRequest, FunctionCreate, FunctionOut, SkillCreate, SkillOut,
)
from .llm import LLMClient, LLMError, MockLLM, OpenAICompatibleClient
from .custom_functions import CustomFunction, CustomFunctionRegistry
from .llm_config import LLMConfig, LLMConfigStore
from .models import (
    BudgetProfile, MemoryFilter, NodeStatus, Persona, RuleScope, RuleStatus, SelfGeneratedRule, now_ts,
)
from .orchestrator import Orchestrator
from .personas import PersonaRegistry, RulesManager, default_personas
from .skills import Skill, default_skill_catalog
from .services import ServiceRunner, ServiceStore, services_enabled
from .storage import Storage
from .time_utils import calculate_time_delta
from .tools import BRAIN_ONLY_TOOLS, TOOL_SCHEMAS, ToolDispatcher, ToolError


def _iso_to_ts(s: Optional[str], end_of_day: bool = False) -> Optional[float]:
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"Invalid ISO date: {s!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ts = dt.timestamp()
    if end_of_day and len(s) <= 10:  # bare date as upper bound = through end of that day
        ts += 86399.999
    return ts


def build_llm_client(config: LLMConfig, model: Optional[str] = None) -> LLMClient:
    """Build a client from `config`. Pass `model` to override which model
    id is used (e.g. the summarizer model) while keeping base_url/api_key/
    generation params shared with the main config."""
    effective_model = model if model is not None else config.model
    if not (config.base_url and effective_model):
        return MockLLM()
    extra_headers = {}
    if config.site_url:
        extra_headers["HTTP-Referer"] = config.site_url
    if config.site_name:
        extra_headers["X-Title"] = config.site_name
    return OpenAICompatibleClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=effective_model,
        extra_headers=extra_headers,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        top_p=config.top_p,
        context_window=config.context_window,
        reasoning_enabled=config.reasoning_enabled,
        reasoning_effort=config.reasoning_effort,
        top_k=config.top_k,
        frequency_penalty=config.frequency_penalty,
        presence_penalty=config.presence_penalty,
    )


def build_summarizer_llm_client(config: LLMConfig) -> LLMClient:
    return build_llm_client(config, model=config.effective_summarizer_model())


def build_verifier_llm_client(config: LLMConfig) -> LLMClient:
    return build_llm_client(config, model=config.effective_verify_model())


def build_vision_llm_client(config: LLMConfig) -> Optional[LLMClient]:
    if not config.effective_vision_model():
        return None
    return build_llm_client(config, model=config.effective_vision_model())


def build_llm_from_env() -> LLMClient:
    return build_llm_client(_llm_config_from_env())


def _llm_config_from_env() -> LLMConfig:
    def _float_env(name: str, default: float) -> float:
        try:
            return float(os.environ[name])
        except (KeyError, ValueError):
            return default

    def _int_env(name: str, default: int) -> int:
        try:
            return int(os.environ[name])
        except (KeyError, ValueError):
            return default

    top_p_raw = os.environ.get("PAI_LLM_TOP_P", "")
    top_p = None
    if top_p_raw:
        try:
            top_p = float(top_p_raw)
        except ValueError:
            top_p = None

    reasoning_effort = os.environ.get("PAI_LLM_REASONING_EFFORT", "medium") or "medium"
    if reasoning_effort not in ("low", "medium", "high"):
        reasoning_effort = "medium"

    return LLMConfig(
        base_url=os.environ.get("PAI_LLM_BASE_URL", ""),
        api_key=os.environ.get("PAI_LLM_API_KEY", ""),
        model=os.environ.get("PAI_LLM_MODEL", ""),
        summarizer_model=os.environ.get("PAI_LLM_SUMMARIZER_MODEL", ""),
        site_url=os.environ.get("PAI_LLM_SITE_URL", ""),
        site_name=os.environ.get("PAI_LLM_SITE_NAME", ""),
        temperature=_float_env("PAI_LLM_TEMPERATURE", 0.3),
        max_tokens=_int_env("PAI_LLM_MAX_TOKENS", 1024),
        top_p=top_p,
        context_window=_int_env("PAI_LLM_CONTEXT_WINDOW", 8192),
        reasoning_enabled=os.environ.get("PAI_LLM_REASONING_ENABLED", "").lower() in ("1", "true", "yes"),
        reasoning_effort=reasoning_effort,
        compression_trigger_pct=max(1, min(_int_env("PAI_COMPRESSION_TRIGGER_PCT", 50), 95)),
    )


def _tail_length_from_env() -> int:
    try:
        return max(2, min(int(os.environ.get("PAI_TAIL_LENGTH", "10")), 100))
    except ValueError:
        return 10


def _ensure_pai_logging() -> None:
    """Show pai.* INFO messages (e.g. answer checks) on the console. Idempotent."""
    import logging
    log = logging.getLogger("pai")
    if log.level == logging.NOTSET or log.level > logging.INFO:
        log.setLevel(logging.INFO)
    if not any(getattr(h, "_pai", False) for h in log.handlers):
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        h._pai = True
        log.addHandler(h)


def create_app(
    data_dir: str = "",
    llm: Optional[LLMClient] = None,
    storage: Optional[Storage] = None,
) -> FastAPI:
    data_dir = data_dir or os.environ.get("PAI_DATA_DIR", "./pai_data")
    _ensure_pai_logging()
    app = FastAPI(title="Personal AI Assistant — API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if STATIC_DIR.exists():
        app.mount("/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            js_v = int((STATIC_DIR / "app.js").stat().st_mtime)
            css_v = int((STATIC_DIR / "style.css").stat().st_mtime)
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            html = html.replace("app.js?v=__BUILD__", f"app.js?v={js_v}")
            html = html.replace("style.css?v=__BUILD__", f"style.css?v={css_v}")
            return Response(
                content=html,
                media_type="text/html",
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )

    app.state.storage = storage or Storage(data_dir)
    app.state.llm_config_store = LLMConfigStore(data_dir)

    if llm is not None:
        app.state.llm = llm
        saved_config = app.state.llm_config_store.load()
        app.state.summarizer_llm = llm
        app.state.verifier_llm = llm
        app.state.vision_llm = None
    else:
        saved_config = app.state.llm_config_store.load()
        if saved_config.is_configured():
            app.state.llm = build_llm_client(saved_config)
        else:
            app.state.llm = build_llm_from_env()
            env_config = _llm_config_from_env()
            if env_config.is_configured():
                app.state.llm_config_store.save(env_config)
                saved_config = env_config
        app.state.summarizer_llm = build_summarizer_llm_client(saved_config)
        app.state.verifier_llm = build_verifier_llm_client(saved_config)
        app.state.vision_llm = build_vision_llm_client(saved_config)

    personas_path = Path(data_dir) / "personas.json"
    skills_path = Path(data_dir) / "skills.json"
    app.state.personas = PersonaRegistry(personas_path)
    if not app.state.personas.all():
        # first run / no saved file yet — seed with built-in defaults, persisted from now on
        for p in default_personas().all():
            app.state.personas.register(p)
        app.state.personas.path = personas_path
        app.state.personas.save()
    app.state.personas.path = personas_path
    app.state.skills = default_skill_catalog(skills_path)
    app.state.rules = RulesManager(Path(data_dir) / "rules.json")
    app.state.custom_functions = CustomFunctionRegistry(Path(data_dir) / "functions.json")

    known_ctx_len = saved_config.context_window if saved_config.is_configured() else None

    app.state.orchestrator = Orchestrator(
        app.state.storage, app.state.llm, app.state.personas, app.state.skills, app.state.rules,
        custom_functions=app.state.custom_functions,
        llm_base_url=saved_config.base_url or None,
        known_context_length=known_ctx_len,
        max_tool_iterations=saved_config.max_tool_iterations,
        summarizer_llm=app.state.summarizer_llm,
        verifier_llm=app.state.verifier_llm,
        verify_strictness=saved_config.verify_strictness,
        vision_llm=app.state.vision_llm,
        target_free_ratio=saved_config.target_free_ratio(),
        timezone_name=os.environ.get("PAI_TIMEZONE", ""),
        tail_length=_tail_length_from_env(),
    )

    def get_orchestrator() -> Orchestrator:
        return app.state.orchestrator

    def get_storage() -> Storage:
        return app.state.storage

    def get_personas() -> PersonaRegistry:
        return app.state.personas

    def get_rules() -> RulesManager:
        return app.state.rules

    def get_custom_functions() -> CustomFunctionRegistry:
        return app.state.custom_functions

    # ---------- autonomous services ----------
    app.state.services = ServiceStore(Path(data_dir) / "services.json")

    def _execute_service(svc: dict) -> dict:
        if app.state.personas.get(svc["persona_id"]) is None:
            raise RuntimeError(f"персона '{svc['persona_id']}' не найдена")
        origin = {"kind": "service", "id": svc["id"], "name": svc["name"]}
        result = app.state.orchestrator.handle_turn(svc["prompt"], svc["persona_id"], svc["space_id"], origin=origin)
        return {"reply": result.reply}

    app.state.service_runner = ServiceRunner(app.state.services, _execute_service)
    if services_enabled():
        app.state.service_runner.start()

    def _service_view(svc: dict) -> dict:
        return {**svc, "running": app.state.service_runner.is_running(svc["id"])}

    @app.get("/services")
    def list_services():
        return [_service_view(s) for s in app.state.services.list()]

    @app.get("/services/{sid}")
    def get_service(sid: str):
        svc = app.state.services.get(sid)
        if svc is None:
            raise HTTPException(404, "service not found")
        return _service_view(svc)

    def _check_persona(fields: dict) -> None:
        pid = str(fields.get("persona_id", "")).strip()
        if pid and app.state.personas.get(pid) is None:
            raise HTTPException(400, f"персона '{pid}' не найдена")

    @app.post("/services", status_code=201)
    def create_service(payload: dict = Body(...)):
        _check_persona(payload)
        try:
            svc = app.state.services.create(payload, time.time())
        except ValueError as e:
            raise HTTPException(400, str(e))
        return _service_view(svc)

    @app.put("/services/{sid}")
    def update_service(sid: str, payload: dict = Body(...)):
        _check_persona(payload)
        try:
            svc = app.state.services.update(sid, payload, time.time())
        except ValueError as e:
            raise HTTPException(400, str(e))
        if svc is None:
            raise HTTPException(404, "service not found")
        return _service_view(svc)

    @app.delete("/services/{sid}")
    def delete_service(sid: str):
        if not app.state.services.delete(sid):
            raise HTTPException(404, "service not found")
        return {"status": "deleted"}

    @app.post("/services/{sid}/run", status_code=202)
    def run_service_now(sid: str):
        if app.state.services.get(sid) is None:
            raise HTTPException(404, "service not found")
        started = app.state.service_runner.start_run(sid) is not None
        return {"started": started, "already_running": not started}

    @app.get("/health")
    def health():
        return {"status": "ok"}

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
            verify_model=(payload.verify_model if payload.verify_model is not None else current.verify_model).strip(),
            vision_model=(payload.vision_model if payload.vision_model is not None else current.vision_model).strip(),
            verify_strictness=(payload.verify_strictness if payload.verify_strictness is not None else current.verify_strictness),
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
            raise HTTPException(500, f"Failed to save settings: {e}")
        new_llm = build_llm_client(new_config)
        new_summarizer_llm = build_summarizer_llm_client(new_config)
        new_verifier_llm = build_verifier_llm_client(new_config)
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
            vision_llm=new_vision_llm,
            target_free_ratio=new_config.target_free_ratio(),
        )
        return {
            **new_config.masked(),
            "effective_summarizer_model": new_config.effective_summarizer_model(),
            "effective_verify_model": new_config.effective_verify_model(),
            "effective_vision_model": new_config.effective_vision_model(),
            "active": isinstance(new_llm, OpenAICompatibleClient),
            "using_mock": isinstance(new_llm, MockLLM),
            "summarizer_active": isinstance(new_summarizer_llm, OpenAICompatibleClient),
        }

    @app.get("/settings/llm/models")
    def list_llm_models(base_url: Optional[str] = None, api_key: Optional[str] = None):
        current = app.state.llm_config_store.load()
        effective_base_url = (base_url or current.base_url).strip()
        effective_api_key = current.api_key if api_key is None else api_key
        if not effective_base_url:
            raise HTTPException(400, "Please provide the provider base URL")
        probe = OpenAICompatibleClient(base_url=effective_base_url, api_key=effective_api_key, model="")
        try:
            models = probe.list_models()
        except LLMError as e:
            raise HTTPException(502, f"Failed to fetch the list of models: {e}")
        return {"models": models}

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, orch: Orchestrator = Depends(get_orchestrator)):
        ref_now = None
        if req.reference_now:
            try:
                ref_now = datetime.fromisoformat(req.reference_now)
                if ref_now.tzinfo is None:
                    ref_now = ref_now.replace(tzinfo=timezone.utc)
            except ValueError:
                raise HTTPException(400, f"Invalid reference_now: {req.reference_now}")
        try:
            result = orch.handle_turn(
                req.text, persona_id=req.persona_id, space_id=req.space_id, reference_now=ref_now,
                images=req.images or None,
            )
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ToolError as e:
            raise HTTPException(403, str(e))
        except LLMError as e:
            raise HTTPException(502, f"LLM provider error: {e}")
        return ChatResponse(
            reply=result.reply,
            reasoning=result.reasoning,
            tool_trace=result.tool_trace,
            live_context_preview=result.live_context_preview,
            usage=result.usage,
            cache_status=result.cache_status,
        )

    @app.post("/chat/stream")
    def chat_stream(req: ChatRequest, orch: Orchestrator = Depends(get_orchestrator)):
        ref_now = None
        if req.reference_now:
            try:
                ref_now = datetime.fromisoformat(req.reference_now)
                if ref_now.tzinfo is None:
                    ref_now = ref_now.replace(tzinfo=timezone.utc)
            except ValueError:
                raise HTTPException(400, f"Invalid reference_now: {req.reference_now}")

        def gen():
            try:
                for event in orch.handle_turn_stream(
                    req.text, persona_id=req.persona_id, space_id=req.space_id, reference_now=ref_now,
                    images=req.images or None,
                ):
                    yield f"data: {_json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            except KeyError as e:
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            except ToolError as e:
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            except LLMError as e:
                yield f"data: {_json.dumps({'type': 'error', 'message': f'LLM provider error: {e}'}, ensure_ascii=False)}\n\n"
            except Exception as e:  # never let the stream die silently
                yield f"data: {_json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/cache/status")
    def cache_status(orch: Orchestrator = Depends(get_orchestrator)):
        return orch.get_cache_status()

    @app.get("/chat/history")
    def chat_history(
        space_id: str = "personal",
        limit: int = 10,
        before: int | None = None,
        storage: Storage = Depends(get_storage),
    ):
        nodes = storage.get_chat_messages(
            space_id=space_id,
            limit=limit,
            before_position=before,
        )
        nodes.reverse()
        out = []
        for n in nodes:
            role = "assistant" if (n.tags and "assistant" in n.tags) else "user"
            tags = n.tags or []
            origin = "service" if "service" in tags else None
            origin_name = next((t[len("service-name:"):] for t in tags if t.startswith("service-name:")), None)
            out.append({
                "id": n.id,
                "position": n.position_in_history,
                "role": role,
                "origin": origin,
                "origin_name": origin_name,
                "text": n.raw_content,
                "reasoning": n.reasoning if role == "assistant" else None,
                "occurred_at": n.occurred_at,
            })
        next_cursor = out[0]["position"] if out else None
        return {
            "space_id": space_id,
            "messages": out,
            "next_cursor": next_cursor,
            "has_more": len(out) == limit,
        }

    @app.get("/tools")
    def list_tools(functions: CustomFunctionRegistry = Depends(get_custom_functions)):
        """Return built-in tools plus all active custom tools for persona configuration."""
        out = [
            {"name": s["function"]["name"], "description": s["function"].get("description", "")}
            for s in TOOL_SCHEMAS if s["function"]["name"] not in BRAIN_ONLY_TOOLS
        ]
        out.extend(
            {"name": f.name, "description": f.description}
            for f in functions.active()
        )
        return out

    @app.get("/skills")
    def list_skills(skills=Depends(lambda: app.state.skills)):
        return [
            {
                "skill_id": s.skill_id,
                "trigger_description": s.trigger_description,
                "full_instruction_text": s.full_instruction_text,
                "version": s.version,
                "last_updated": s.last_updated,
            }
            for s in skills.all()
        ]

    @app.post("/skills", response_model=SkillOut, status_code=201)
    def create_skill(req: SkillCreate, skills=Depends(lambda: app.state.skills)):
        if skills.get(req.skill_id) is not None:
            raise HTTPException(409, f"Skill '{req.skill_id}' already exists")
        skill = Skill(
            skill_id=req.skill_id,
            trigger_description=req.trigger_description,
            full_instruction_text=req.full_instruction_text,
        )
        skills.register(skill)
        return SkillOut(
            skill_id=skill.skill_id, trigger_description=skill.trigger_description,
            full_instruction_text=skill.full_instruction_text, version=skill.version,
            last_updated=skill.last_updated,
        )

    @app.delete("/skills/{skill_id}", status_code=204)
    def delete_skill(
        skill_id: str,
        skills=Depends(lambda: app.state.skills),
        personas: PersonaRegistry = Depends(get_personas),
    ):
        if skills.get(skill_id) is None:
            raise HTTPException(404, f"Skill '{skill_id}' not found")
        skills.remove(skill_id)
        # Detach the skill from any persona that referenced it, so it doesn't
        # linger as a dangling id in active_skill_ids.
        for persona in personas.all():
            if skill_id in persona.active_skill_ids:
                persona.active_skill_ids = [s for s in persona.active_skill_ids if s != skill_id]
                personas.register(persona)

    def _persona_out(p: Persona) -> PersonaOut:
        return PersonaOut(
            persona_id=p.persona_id, name=p.name, description=p.description,
            system_core_addon=p.system_core_addon,
            active_skill_ids=p.active_skill_ids,
            allow_spaces=p.memory_filter.allow_spaces,
            deny_spaces=p.memory_filter.deny_spaces,
            allow_tags=p.memory_filter.allow_tags,
            deny_tags=p.memory_filter.deny_tags,
            tone=p.tone, tool_permissions=p.tool_permissions,
            budget_profile=BudgetProfileUpdate(**p.budget_profile.to_dict()),
        )

    @app.get("/personas", response_model=list[PersonaOut])
    def list_personas(personas: PersonaRegistry = Depends(get_personas)):
        return [_persona_out(p) for p in personas.all()]

    @app.post("/personas", response_model=PersonaOut, status_code=201)
    def create_persona(
        req: PersonaCreate,
        personas: PersonaRegistry = Depends(get_personas),
        functions: CustomFunctionRegistry = Depends(get_custom_functions),
    ):
        if personas.get(req.persona_id) is not None:
            raise HTTPException(409, f"Persona '{req.persona_id}' already exists")
        known_tools = {s["function"]["name"] for s in TOOL_SCHEMAS if s["function"]["name"] not in BRAIN_ONLY_TOOLS}
        known_tools.update(f.name for f in functions.active())
        unknown = [t for t in req.tool_permissions if t not in known_tools]
        if unknown:
            raise HTTPException(
                422, f"Unknown tool(s): {', '.join(unknown)}. Known: {sorted(known_tools)}"
            )
        persona = Persona(
            persona_id=req.persona_id,
            name=req.name,
            description=req.description,
            system_core_addon=req.system_core_addon,
            active_skill_ids=req.active_skill_ids,
            memory_filter=MemoryFilter(
                allow_spaces=req.allow_spaces, deny_spaces=req.deny_spaces,
                allow_tags=req.allow_tags, deny_tags=req.deny_tags,
            ),
            tool_permissions=req.tool_permissions,
            budget_profile=BudgetProfile(),
            tone=req.tone,
        )
        personas.register(persona)
        return _persona_out(persona)

    @app.patch("/personas/{persona_id}", response_model=PersonaOut)
    def update_persona(
        persona_id: str,
        req: PersonaUpdate,
        personas: PersonaRegistry = Depends(get_personas),
        functions: CustomFunctionRegistry = Depends(get_custom_functions),
    ):
        persona = personas.get(persona_id)
        if persona is None:
            raise HTTPException(404, f"Persona '{persona_id}' not found")
        if req.tool_permissions is not None:
            known_tools = {s["function"]["name"] for s in TOOL_SCHEMAS if s["function"]["name"] not in BRAIN_ONLY_TOOLS}
            known_tools.update(f.name for f in functions.active())
            unknown = [t for t in req.tool_permissions if t not in known_tools]
            if unknown:
                raise HTTPException(
                    422, f"Unknown tool(s): {', '.join(unknown)}. Known: {sorted(known_tools)}"
                )
            persona.tool_permissions = req.tool_permissions
        if req.name is not None:
            persona.name = req.name
        if req.description is not None:
            persona.description = req.description
        if req.system_core_addon is not None:
            persona.system_core_addon = req.system_core_addon
        if req.active_skill_ids is not None:
            persona.active_skill_ids = req.active_skill_ids
        if req.tone is not None:
            persona.tone = req.tone
        if req.budget_profile is not None:
            for key, value in req.budget_profile.model_dump(exclude_none=True).items():
                setattr(persona.budget_profile, key, value)
        if req.allow_spaces is not None:
            persona.memory_filter.allow_spaces = req.allow_spaces
        if req.deny_spaces is not None:
            persona.memory_filter.deny_spaces = req.deny_spaces
        if req.allow_tags is not None:
            persona.memory_filter.allow_tags = req.allow_tags
        if req.deny_tags is not None:
            persona.memory_filter.deny_tags = req.deny_tags
        persona.updated_at = now_ts()
        personas.register(persona)
        return _persona_out(persona)

    @app.delete("/personas/{persona_id}", status_code=204)
    def delete_persona(persona_id: str, personas: PersonaRegistry = Depends(get_personas)):
        persona = personas.get(persona_id)
        if persona is None:
            raise HTTPException(404, f"Persona '{persona_id}' not found")
        personas.remove(persona_id)

    @app.patch("/personas/{persona_id}/tools", response_model=PersonaOut)
    def update_persona_tools(
        persona_id: str,
        req: PersonaToolsUpdate,
        personas: PersonaRegistry = Depends(get_personas),
        functions: CustomFunctionRegistry = Depends(get_custom_functions),
    ):
        persona = personas.get(persona_id)
        if persona is None:
            raise HTTPException(404, f"Persona '{persona_id}' not found")
        known_tools = {s["function"]["name"] for s in TOOL_SCHEMAS if s["function"]["name"] not in BRAIN_ONLY_TOOLS}
        known_tools.update(f.name for f in functions.active())
        unknown = [t for t in req.tool_permissions if t not in known_tools]
        if unknown:
            raise HTTPException(
                422, f"Unknown tool(s): {', '.join(unknown)}. Known: {sorted(known_tools)}"
            )
        persona.tool_permissions = req.tool_permissions
        persona.updated_at = now_ts()
        personas.register(persona)
        return _persona_out(persona)

    @app.get("/spaces")
    def list_spaces(storage: Storage = Depends(get_storage)):
        return storage.stats()["by_space"]

    @app.get("/memory/stats")
    def memory_stats(storage: Storage = Depends(get_storage), orch: Orchestrator = Depends(get_orchestrator)):
        stats = storage.stats()
        stats["compression"] = orch.tree.compression_ratio()
        return stats

    def _node_to_out(n) -> NodeOut:
        return NodeOut(
            id=n.id, space_id=n.space_id, level=n.level, parent_id=n.parent_id,
            child_ids=n.child_ids, type=n.type.value if hasattr(n.type, "value") else n.type,
            summary_text=n.summary_text, raw_content=n.raw_content, occurred_at=n.occurred_at,
            timeframe_start=n.timeframe_start, timeframe_end=n.timeframe_end, tags=n.tags,
            status=n.status.value if hasattr(n.status, "value") else n.status,
            retrieval_count=n.retrieval_count, position_in_history=n.position_in_history,
        )

    @app.get("/memory/tree/roots", response_model=list[NodeOut])
    def tree_roots(space_id: Optional[str] = None, orch: Orchestrator = Depends(get_orchestrator)):
        tops = orch.tree.get_tree_tops(space_id=space_id or "personal", max_nodes=50)
        return [_node_to_out(n) for n in tops]

    @app.get("/memory/tree/{node_id}/children", response_model=list[NodeOut])
    def tree_children(node_id: str, storage: Storage = Depends(get_storage)):
        return [_node_to_out(n) for n in storage.get_children(node_id)]

    @app.get("/memory/node/{node_id}", response_model=NodeOut)
    def get_node(node_id: str, storage: Storage = Depends(get_storage)):
        node = storage.get_fragment(node_id, record_retrieval=False)
        if node is None:
            raise HTTPException(404, f"No such node: {node_id}")
        return _node_to_out(node)

    @app.patch("/memory/node/{node_id}/status", response_model=NodeOut)
    def set_node_status(node_id: str, req: NodeStatusUpdate, storage: Storage = Depends(get_storage)):
        try:
            status = NodeStatus(req.status)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {req.status}")
        try:
            storage.set_status(node_id, status)
        except KeyError as e:
            raise HTTPException(404, str(e))
        return _node_to_out(storage.get_fragment(node_id, record_retrieval=False))

    @app.post("/memory/search")
    def search(req: SearchRequest, storage: Storage = Depends(get_storage)):
        results = storage.search_archive(
            req.query, space_id=req.space_id,
            date_from=_iso_to_ts(req.date_from), date_to=_iso_to_ts(req.date_to, end_of_day=True),
            limit=req.limit,
        )
        return {"results": results}

    @app.post("/memory/compress")
    def compress(req: CompressRequest, orch: Orchestrator = Depends(get_orchestrator)):
        created = orch.run_background_compression(space_id=req.space_id, force_top=req.force_top)
        return {"created_node_ids": [n.id for n in created], "count": len(created)}

    @app.get("/actions/log")
    def action_log(
        limit: int = 10, kind: Optional[str] = None, space_id: Optional[str] = None,
        storage: Storage = Depends(get_storage),
    ):
        return storage.get_action_log(limit=limit, kind=kind, space_id=space_id)

    @app.get("/memory/retrieval-log")
    def retrieval_log(limit: int = 100, storage: Storage = Depends(get_storage)):
        return storage.get_retrieval_log(limit=limit)

    @app.get("/memory/export")
    def export_memory(storage: Storage = Depends(get_storage)):
        data = storage.export_wal()
        filename = f"pai_backup_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
        return Response(
            content=data,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/memory/import")
    async def import_memory(
        file: UploadFile = File(...),
        mode: str = Query("merge", pattern="^(merge|replace)$"),
        storage: Storage = Depends(get_storage),
    ):
        data = await file.read()
        try:
            count = storage.import_wal(data, mode=mode)
        except ValueError as e:
            raise HTTPException(400, f"Invalid backup file: {e}")
        return {"mode": mode, "nodes_in_index": count}

    @app.post("/tools/calculate_time_delta")
    def tool_time_delta(req: TimeDeltaRequest):
        try:
            return calculate_time_delta(req.date1, req.date2)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/functions", response_model=list[FunctionOut])
    def list_functions(functions: CustomFunctionRegistry = Depends(get_custom_functions)):
        return [FunctionOut(**f.to_dict()) for f in functions.all()]

    @app.post("/functions", response_model=FunctionOut, status_code=201)
    def create_function(req: FunctionCreate, functions: CustomFunctionRegistry = Depends(get_custom_functions)):
        try:
            fn = functions.add(CustomFunction(
                name=req.name, description=req.description, parameters=req.parameters,
                kind=req.kind, implementation=req.implementation,
            ), validate=False)
            return FunctionOut(**fn.to_dict())
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/functions/{function_id}/validate", response_model=FunctionOut)
    def validate_function(function_id: str, functions: CustomFunctionRegistry = Depends(get_custom_functions)):
        try:
            return FunctionOut(**functions.validate(function_id).to_dict())
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/functions/{function_id}/activate", response_model=FunctionOut)
    def activate_function(function_id: str, functions: CustomFunctionRegistry = Depends(get_custom_functions)):
        try:
            return FunctionOut(**functions.activate(function_id).to_dict())
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/functions/{function_id}/archive", response_model=FunctionOut)
    def archive_function(function_id: str, functions: CustomFunctionRegistry = Depends(get_custom_functions)):
        try:
            return FunctionOut(**functions.archive(function_id).to_dict())
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/functions/{function_id}/reject", response_model=FunctionOut)
    def reject_function(function_id: str, functions: CustomFunctionRegistry = Depends(get_custom_functions)):
        try:
            return FunctionOut(**functions.reject(function_id).to_dict())
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/functions/{function_id}", status_code=204)
    def delete_function(
        function_id: str,
        functions: CustomFunctionRegistry = Depends(get_custom_functions),
        personas: PersonaRegistry = Depends(get_personas),
    ):
        fn = functions.get(function_id)
        if fn is None:
            raise HTTPException(404, f"No such function: {function_id}")
        functions.remove(function_id)
        # Detach the function's tool name from any persona that had it permitted,
        # so it doesn't linger as a dangling permission.
        for persona in personas.all():
            if fn.name in persona.tool_permissions:
                persona.tool_permissions = [t for t in persona.tool_permissions if t != fn.name]
                personas.register(persona)

    @app.get("/rules", response_model=list[RuleOut])
    def list_rules(rules: RulesManager = Depends(get_rules)):
        return [
            RuleOut(
                id=r.id, scope=r.scope.value, persona_id=r.persona_id, space_id=r.space_id,
                trigger_context=r.trigger_context, rule_text=r.rule_text, confidence=r.confidence,
                status=r.status.value, created_at=r.created_at, validated_at=r.validated_at,
            )
            for r in rules.all()
        ]

    @app.post("/rules", response_model=RuleOut, status_code=201)
    def create_rule(req: RuleCreate, rules: RulesManager = Depends(get_rules)):
        try:
            scope = RuleScope(req.scope)
        except ValueError:
            raise HTTPException(400, f"Invalid scope: {req.scope}")
        rule = SelfGeneratedRule(
            scope=scope, persona_id=req.persona_id, space_id=req.space_id,
            trigger_context=req.trigger_context, rule_text=req.rule_text, confidence=req.confidence,
        )
        rules.add(rule)
        return RuleOut(
            id=rule.id, scope=rule.scope.value, persona_id=rule.persona_id, space_id=rule.space_id,
            trigger_context=rule.trigger_context, rule_text=rule.rule_text, confidence=rule.confidence,
            status=rule.status.value, created_at=rule.created_at, validated_at=rule.validated_at,
        )

    def _rule_out(r: SelfGeneratedRule) -> RuleOut:
        return RuleOut(
            id=r.id, scope=r.scope.value, persona_id=r.persona_id, space_id=r.space_id,
            trigger_context=r.trigger_context, rule_text=r.rule_text, confidence=r.confidence,
            status=r.status.value, created_at=r.created_at, validated_at=r.validated_at,
        )

    @app.post("/rules/{rule_id}/validate", response_model=RuleOut)
    def validate_rule(rule_id: str, rules: RulesManager = Depends(get_rules)):
        try:
            return _rule_out(rules.validate(rule_id))
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/rules/{rule_id}/activate", response_model=RuleOut)
    def activate_rule(rule_id: str, rules: RulesManager = Depends(get_rules)):
        try:
            return _rule_out(rules.activate(rule_id))
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/rules/{rule_id}/reject", response_model=RuleOut)
    def reject_rule(rule_id: str, rules: RulesManager = Depends(get_rules)):
        try:
            return _rule_out(rules.reject(rule_id))
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.post("/rules/{rule_id}/archive", response_model=RuleOut)
    def archive_rule(rule_id: str, rules: RulesManager = Depends(get_rules)):
        try:
            return _rule_out(rules.archive(rule_id))
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.delete("/rules/{rule_id}", status_code=204)
    def delete_rule(rule_id: str, rules: RulesManager = Depends(get_rules)):
        try:
            rules.remove(rule_id)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.get("/usage")
    def usage(orch: Orchestrator = Depends(get_orchestrator)):
        summary = orch.usage.summary()
        # OpenRouter's /models is the live pricing/context source. Cache briefly
        # so refreshing the Usage page does not hammer the provider.
        pricing = {}
        llm = app.state.llm
        now = time.time()
        cache = getattr(app.state, "_openrouter_model_cache", None)
        if isinstance(llm, OpenAICompatibleClient):
            if not cache or now - cache.get("ts", 0) > 300:
                try:
                    models = llm.list_models()
                    pricing = {m["id"]: m for m in models}
                    app.state._openrouter_model_cache = {"ts": now, "models": pricing}
                except Exception:
                    pricing = (cache or {}).get("models", {})
            else:
                pricing = cache.get("models", {})

        model_rows = {}
        for event in summary["events"]:
            mid = event.get("model") or getattr(llm, "model", "")
            meta = pricing.get(mid) or {}
            event["model"] = mid
            event["context_length"] = event.get("context_length") or meta.get("context_length")
            event["prompt_price_per_1m"] = meta.get("price_prompt_per_1m", event.get("prompt_price_per_1m"))
            event["completion_price_per_1m"] = meta.get("price_completion_per_1m", event.get("completion_price_per_1m"))
            event["context_utilization"] = (
                round(event["input_tokens"] / event["context_length"], 4)
                if event.get("context_length") else None
            )
            row = model_rows.setdefault(mid, {
                "model": mid, "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cost_usd": 0.0, "context_length": event.get("context_length"),
                "prompt_price_per_1m": event.get("prompt_price_per_1m"),
                "completion_price_per_1m": event.get("completion_price_per_1m"),
            })
            row["calls"] += 1
            row["input_tokens"] += event["input_tokens"]
            row["output_tokens"] += event["output_tokens"]
            row["cost_usd"] += event["cost_usd"]
            row["context_length"] = row["context_length"] or event.get("context_length")
        for row in model_rows.values():
            row["total_tokens"] = row["input_tokens"] + row["output_tokens"]
            row["avg_input_tokens"] = round(row["input_tokens"] / row["calls"]) if row["calls"] else 0
            row["avg_output_tokens"] = round(row["output_tokens"] / row["calls"]) if row["calls"] else 0
            row["cost_usd"] = round(row["cost_usd"], 8)

        summary["models"] = list(model_rows.values())
        summary["pricing_source"] = "openrouter /models" if pricing else "provider response / local fallback"
        summary["pricing_updated_at"] = cache.get("ts") if cache else None
        summary["active_model"] = getattr(llm, "model", "")
        return summary

    return app


app = create_app()
