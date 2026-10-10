"""Tool catalogue and built-in tool endpoints."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from ...api_schemas import (
    TimeDeltaRequest,
)
from ...custom_functions import CustomFunctionRegistry
from ...time_utils import calculate_time_delta
from ...tools import BRAIN_ONLY_TOOLS, TOOL_SCHEMAS
from ..deps import get_custom_functions


def register(app: FastAPI) -> None:
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


    @app.post("/tools/calculate_time_delta")
    def tool_time_delta(req: TimeDeltaRequest):
        try:
            return calculate_time_delta(req.date1, req.date2)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
