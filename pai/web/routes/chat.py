"""Chat endpoints: blocking, streaming, cache status and history."""
from __future__ import annotations

import json as _json
import logging
import threading
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse

from ...api_schemas import (
    ChatRequest,
    ChatResponse,
)
from ...llm import LLMError
from ...orchestrator import Orchestrator
from ...storage import Storage
from ...tools import ToolError
from ...turn_control import begin, cancel, end
from ..deps import get_orchestrator, get_storage

_log = logging.getLogger("pai.web")


class StopRequest(BaseModel):
    turn_id: str = Field(default="", max_length=64)


def register(app: FastAPI) -> None:
    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, orch: Orchestrator = Depends(get_orchestrator)):
        ref_now = None
        if req.reference_now:
            try:
                ref_now = datetime.fromisoformat(req.reference_now)
                if ref_now.tzinfo is None:
                    ref_now = ref_now.replace(tzinfo=timezone.utc)
            except ValueError:
                raise HTTPException(400, f"Invalid reference_now: {req.reference_now}") from None
        try:
            result = orch.handle_turn(
                req.text, persona_id=req.persona_id, space_id=req.space_id, reference_now=ref_now,
                images=req.images or None,
                file_ids=req.file_ids or None,
            )
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ToolError as e:
            raise HTTPException(403, str(e)) from e
        except LLMError as e:
            raise HTTPException(502, f"LLM provider error: {e}") from e
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
                raise HTTPException(400, f"Invalid reference_now: {req.reference_now}") from None

        turn_id = req.turn_id or None
        cancel_ev = begin(turn_id) if turn_id else threading.Event()

        def gen():
            turn = None
            try:
                turn = orch.handle_turn_stream(
                    req.text, persona_id=req.persona_id, space_id=req.space_id, reference_now=ref_now,
                    images=req.images or None,
                    file_ids=req.file_ids or None,
                )
                for event in turn:
                    if cancel_ev.is_set():
                        yield f"data: {_json.dumps({'type': 'stopped'}, ensure_ascii=False)}\n\n"
                        break
                    yield f"data: {_json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            except KeyError as e:
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            except ToolError as e:
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            except LLMError as e:
                yield f"data: {_json.dumps({'type': 'error', 'message': f'LLM provider error: {e}'}, ensure_ascii=False)}\n\n"
            except Exception as e:  # never let the stream die silently
                _log.exception("chat stream failed")
                yield f"data: {_json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'}, ensure_ascii=False)}\n\n"
            finally:
                # closing the turn stops the LLM stream and tool loop at their next yield point
                if turn is not None:
                    turn.close()
                if turn_id:
                    end(turn_id)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/chat/stop")
    def chat_stop(req: StopRequest):
        if not req.turn_id:
            raise HTTPException(400, "turn_id is required")
        cancel(req.turn_id)
        return {"ok": True}

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
