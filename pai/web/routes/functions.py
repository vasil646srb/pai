"""Custom function endpoints."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from ...api_schemas import (
    FunctionCreate,
    FunctionOut,
)
from ...custom_functions import CustomFunction, CustomFunctionRegistry
from ...personas import PersonaRegistry
from ..deps import get_custom_functions, get_personas


def register(app: FastAPI) -> None:
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
            raise HTTPException(400, str(e)) from e

    @app.post("/functions/{function_id}/validate", response_model=FunctionOut)
    def validate_function(function_id: str, functions: CustomFunctionRegistry = Depends(get_custom_functions)):
        try:
            return FunctionOut(**functions.validate(function_id).to_dict())
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/functions/{function_id}/activate", response_model=FunctionOut)
    def activate_function(function_id: str, functions: CustomFunctionRegistry = Depends(get_custom_functions)):
        try:
            return FunctionOut(**functions.activate(function_id).to_dict())
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/functions/{function_id}/archive", response_model=FunctionOut)
    def archive_function(function_id: str, functions: CustomFunctionRegistry = Depends(get_custom_functions)):
        try:
            return FunctionOut(**functions.archive(function_id).to_dict())
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/functions/{function_id}/reject", response_model=FunctionOut)
    def reject_function(function_id: str, functions: CustomFunctionRegistry = Depends(get_custom_functions)):
        try:
            return FunctionOut(**functions.reject(function_id).to_dict())
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

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
