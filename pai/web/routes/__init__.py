"""Route registration: each module exposes register(app)."""
from fastapi import FastAPI

from . import auth, chat, files, functions, guest, memory, personas, rules, services, settings, skills, system, tools


def register_routes(app: FastAPI) -> None:
    for module in (auth, guest, system, services, settings, chat, files, tools, skills, personas, memory, functions, rules):
        module.register(app)
