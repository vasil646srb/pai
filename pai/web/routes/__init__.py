"""Route registration: each module exposes register(app)."""
from fastapi import FastAPI

from . import chat, functions, memory, personas, rules, services, settings, skills, system, tools


def register_routes(app: FastAPI) -> None:
    for module in (system, services, settings, chat, tools, skills, personas, memory, functions, rules):
        module.register(app)
