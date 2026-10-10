"""FastAPI dependency getters that read shared objects from app.state."""
from __future__ import annotations

from fastapi import Request

from ..custom_functions import CustomFunctionRegistry
from ..orchestrator import Orchestrator
from ..personas import PersonaRegistry, RulesManager
from ..storage import Storage


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_personas(request: Request) -> PersonaRegistry:
    return request.app.state.personas


def get_rules(request: Request) -> RulesManager:
    return request.app.state.rules


def get_custom_functions(request: Request) -> CustomFunctionRegistry:
    return request.app.state.custom_functions
