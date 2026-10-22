"""Route registration: each module exposes register(app)."""
from fastapi import FastAPI

from . import auth, chat, data, files, functions, guest, memory, personas, requests, rules, semantic, services, settings, skills, system, tools, transcribe, vault


def register_routes(app: FastAPI) -> None:
    for module in (auth, guest, system, services, settings, chat, files, tools, skills, personas, memory, functions, requests, rules, vault, semantic, transcribe, data):
        module.register(app)
