"""Application settings, read from environment variables in exactly one place.

`Settings.from_env()` is called once when the app is created. Everything else
receives values through constructors, so tests can pass explicit settings
instead of mutating os.environ.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .llm_config import LLMConfig


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env[name])
    except (KeyError, ValueError):
        return default


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(env[name])
    except (KeyError, ValueError):
        return default


def _flag(env: Mapping[str, str], name: str, default: str) -> bool:
    """Feature flags are on unless set to "0"."""
    return env.get(name, default) != "0"


@dataclass(frozen=True)
class Settings:
    data_dir: str = "./pai_data"
    timezone_name: str = ""
    tail_length: int = 10
    verify_enabled: bool = True
    services_enabled: bool = True
    auth_password: str = ""
    auth_disabled: bool = False
    brave_search_api_key: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        try:
            tail = max(2, min(int(env.get("PAI_TAIL_LENGTH", "10")), 100))
        except ValueError:
            tail = 10
        return cls(
            data_dir=env.get("PAI_DATA_DIR") or "./pai_data",
            timezone_name=env.get("PAI_TIMEZONE", ""),
            tail_length=tail,
            verify_enabled=_flag(env, "PAI_VERIFY", "1"),
            services_enabled=_flag(env, "PAI_SERVICES", "1"),
            auth_password=env.get("PAI_PASSWORD", "57969090"),
            auth_disabled=_flag(env, "PAI_AUTH_DISABLED", "0"),
            brave_search_api_key=env.get("BRAVE_SEARCH_API_KEY", "").strip(),
        )


def llm_config_from_env(env: Mapping[str, str] | None = None) -> LLMConfig:
    """Initial LLM configuration from PAI_LLM_* variables (used until settings are saved)."""
    env = os.environ if env is None else env
    top_p = None
    if env.get("PAI_LLM_TOP_P", ""):
        try:
            top_p = float(env["PAI_LLM_TOP_P"])
        except ValueError:
            top_p = None

    reasoning_effort = env.get("PAI_LLM_REASONING_EFFORT", "medium") or "medium"
    if reasoning_effort not in ("low", "medium", "high"):
        reasoning_effort = "medium"

    return LLMConfig(
        base_url=env.get("PAI_LLM_BASE_URL", ""),
        api_key=env.get("PAI_LLM_API_KEY", ""),
        model=env.get("PAI_LLM_MODEL", ""),
        summarizer_model=env.get("PAI_LLM_SUMMARIZER_MODEL", ""),
        site_url=env.get("PAI_LLM_SITE_URL", ""),
        site_name=env.get("PAI_LLM_SITE_NAME", ""),
        temperature=_float(env, "PAI_LLM_TEMPERATURE", 0.3),
        max_tokens=_int(env, "PAI_LLM_MAX_TOKENS", 1024),
        top_p=top_p,
        context_window=_int(env, "PAI_LLM_CONTEXT_WINDOW", 8192),
        reasoning_enabled=env.get("PAI_LLM_REASONING_ENABLED", "").lower() in ("1", "true", "yes"),
        reasoning_effort=reasoning_effort,
        compression_trigger_pct=max(1, min(_int(env, "PAI_COMPRESSION_TRIGGER_PCT", 50), 95)),
    )


def termux_bash_path() -> str:
    """Optional Termux bash binary for the shell tool's `termux` runtime."""
    return os.environ.get("TERMUX_BASH", "")
