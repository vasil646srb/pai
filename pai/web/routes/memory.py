"""Spaces, memory tree, archive, logs and backup endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from ...api_schemas import (
    CompressRequest,
    NodeOut,
    NodeStatusUpdate,
    SearchRequest,
)
from ...models import (
    NodeStatus,
)
from ...orchestrator import Orchestrator
from ...storage import Storage
from ..deps import get_orchestrator, get_storage
from ..util import iso_to_ts


def register(app: FastAPI) -> None:
    @app.get("/spaces")
    def list_spaces(storage: Storage = Depends(get_storage)):
        return storage.stats()["by_space"]

    @app.get("/memory/stats")
    def memory_stats(storage: Storage = Depends(get_storage), orch: Orchestrator = Depends(get_orchestrator)):
        stats = storage.stats()
        stats["compression"] = orch.tree.compression_ratio()
        return stats

    def _node_to_out(n) -> NodeOut:
        return NodeOut(
            id=n.id, space_id=n.space_id, level=n.level, parent_id=n.parent_id,
            child_ids=n.child_ids, type=n.type.value if hasattr(n.type, "value") else n.type,
            summary_text=n.summary_text, raw_content=n.raw_content, occurred_at=n.occurred_at,
            timeframe_start=n.timeframe_start, timeframe_end=n.timeframe_end, tags=n.tags,
            status=n.status.value if hasattr(n.status, "value") else n.status,
            retrieval_count=n.retrieval_count, position_in_history=n.position_in_history,
        )

    @app.get("/memory/tree/roots", response_model=list[NodeOut])
    def tree_roots(space_id: str | None = None, orch: Orchestrator = Depends(get_orchestrator)):
        tops = orch.tree.get_tree_tops(space_id=space_id or "personal", max_nodes=50)
        return [_node_to_out(n) for n in tops]

    @app.get("/memory/tree/{node_id}/children", response_model=list[NodeOut])
    def tree_children(node_id: str, storage: Storage = Depends(get_storage)):
        return [_node_to_out(n) for n in storage.get_children(node_id)]

    @app.get("/memory/node/{node_id}", response_model=NodeOut)
    def get_node(node_id: str, storage: Storage = Depends(get_storage)):
        node = storage.get_fragment(node_id, record_retrieval=False)
        if node is None:
            raise HTTPException(404, f"No such node: {node_id}")
        return _node_to_out(node)

    @app.patch("/memory/node/{node_id}/status", response_model=NodeOut)
    def set_node_status(node_id: str, req: NodeStatusUpdate, storage: Storage = Depends(get_storage)):
        try:
            status = NodeStatus(req.status)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {req.status}") from None
        try:
            storage.set_status(node_id, status)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        return _node_to_out(storage.get_fragment(node_id, record_retrieval=False))

    @app.post("/memory/search")
    def search(req: SearchRequest, storage: Storage = Depends(get_storage)):
        results = storage.search_archive(
            req.query, space_id=req.space_id,
            date_from=iso_to_ts(req.date_from), date_to=iso_to_ts(req.date_to, end_of_day=True),
            limit=req.limit,
        )
        return {"results": results}

    @app.post("/memory/compress")
    def compress(req: CompressRequest, orch: Orchestrator = Depends(get_orchestrator)):
        created = orch.run_background_compression(space_id=req.space_id, force_top=req.force_top)
        return {"created_node_ids": [n.id for n in created], "count": len(created)}

    @app.get("/actions/log")
    def action_log(
        limit: int = 10, kind: str | None = None, space_id: str | None = None,
        storage: Storage = Depends(get_storage),
    ):
        return storage.get_action_log(limit=limit, kind=kind, space_id=space_id)

    @app.get("/memory/retrieval-log")
    def retrieval_log(limit: int = 100, storage: Storage = Depends(get_storage)):
        return storage.get_retrieval_log(limit=limit)

    @app.get("/memory/export")
    def export_memory(storage: Storage = Depends(get_storage)):
        data = storage.export_wal()
        filename = f"pai_backup_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
        return Response(
            content=data,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/memory/import")
    async def import_memory(
        file: UploadFile = File(...),
        mode: str = Query("merge", pattern="^(merge|replace)$"),
        storage: Storage = Depends(get_storage),
    ):
        data = await file.read()
        try:
            count = storage.import_wal(data, mode=mode)
        except ValueError as e:
            raise HTTPException(400, f"Invalid backup file: {e}") from e
        return {"mode": mode, "nodes_in_index": count}
