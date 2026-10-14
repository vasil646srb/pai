"""Application factory: builds LLM clients, storage and the orchestrator, then mounts the web layer."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .custom_functions import CustomFunctionRegistry
from .llm import LLMClient
from .laya import get_laya
from .laya_runtime import LayaRuntime
from .llm_config import LLMConfigStore
from .orchestrator import Orchestrator
from .personas import PersonaRegistry, RulesManager, default_personas
from .services import ServiceStore
from .settings import Settings, llm_config_from_env
from .skills import default_skill_catalog
from .storage import Storage
from .web.builders import (
    build_llm_client,
    build_llm_from_env,
    build_summarizer_llm_client,
    build_verifier_llm_client,
    build_vision_llm_client,
)
from .secret_store import SecretStore
from .tools.web import set_secret_store
from .web.auth import install_auth
from .web.routes import register_routes
from .web.ui import mount_ui
from .web.util import ensure_pai_logging

__all__ = ["create_app", "app"]


def create_app(
    data_dir: str = "",
    llm: LLMClient | None = None,
    storage: Storage | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    data_dir = data_dir or settings.data_dir
    ensure_pai_logging()
    app = FastAPI(title="Personal AI Assistant — API", version="0.1.0")
    origins = [o.strip() for o in os.environ.get("PAI_CORS_ORIGINS", "").split(",") if o.strip()]
    if origins:  # the UI is same-origin; list other origins explicitly, never '*'
        app.add_middleware(CORSMiddleware, allow_origins=origins,
                           allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])
    install_auth(app, data_dir, settings.auth_password, settings.auth_disabled)
    mount_ui(app)

    app.state.storage = storage or Storage(data_dir)
    app.state.llm_config_store = LLMConfigStore(data_dir)
    app.state.secret_store = SecretStore(data_dir)
    set_secret_store(app.state.secret_store)

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
            env_config = llm_config_from_env()
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
        timezone_name=settings.timezone_name,
        tail_length=settings.tail_length,
        verify_enabled=settings.verify_enabled,
        verify_max_checks=saved_config.verify_max_checks,
    )
    app.state.settings = settings
    app.state.services = ServiceStore(Path(data_dir) / "services.json")
    register_routes(app)

    # Laya: find / install / start the local API server in the background (only when the server really runs;
    # TestClient without a context manager and plain imports never fire startup events).
    app.state.laya_runtime = LayaRuntime.from_env(get_laya(), data_dir)
    _inner_lifespan = app.router.lifespan_context  # keeps any existing startup/shutdown handlers working

    @asynccontextmanager
    async def _lifespan(a):
        a.state.laya_runtime.start()  # returns at once: install/start run in a background thread
        try:
            async with _inner_lifespan(a) as state:
                yield state
        finally:
            a.state.laya_runtime.stop()

    app.router.lifespan_context = _lifespan
    return app


app = create_app()
