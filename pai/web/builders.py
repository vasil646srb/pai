"""Builders for LLM clients and configs from settings and environment variables."""
from __future__ import annotations

from ..llm import LLMClient, MockLLM, OpenAICompatibleClient
from ..llm_config import LLMConfig
from ..settings import llm_config_from_env


def build_llm_client(config: LLMConfig, model: str | None = None) -> LLMClient:
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


def build_vision_llm_client(config: LLMConfig) -> LLMClient | None:
    if not config.effective_vision_model():
        return None
    return build_llm_client(config, model=config.effective_vision_model())


def build_llm_from_env() -> LLMClient:
    return build_llm_client(llm_config_from_env())
