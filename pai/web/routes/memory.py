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
    SpaceRename,
)
from ...models import (
    NodeStatus,
)
from ...orchestrator import Orchestrator
from ...storage import Storage
from ...personas import PersonaRegistry
from ..deps import get_orchestrator, get_personas, get_storage
from ..util import iso_to_ts


def register(app: FastAPI) -> None:
    @app.get("/spaces")
    def list_spaces(storage: Storage = Depends(get_storage)):
        return storage.stats()["by_space"]

    @app.get("/memory/spaces", response_model=list[str])
    def memory_spaces(storage: Storage = Depends(get_storage), personas: PersonaRegistry = Depends(get_personas)):
        """Every space the UI should offer: spaces with data, plus spaces any persona is allowed or denied."""
        names = {"personal"}
        names.update(storage.stats()["by_space"].keys())
        for p in personas.all():
            names.update(p.memory_filter.allow_spaces)
        return sorted(n for n in names if n)

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
    def compress(req: CompressRequest, orch: Orchestrator = Depends(get_orchestrator),
                 storage: Storage = Depends(get_storage)):
        # space_id="all": compress every space that has nodes. One failing space does not stop the rest.
        spaces = list(storage.stats()["by_space"].keys()) if req.space_id == "all" else [req.space_id]
        created_ids: list[str] = []
        by_space: dict[str, dict] = {}
        for sid in spaces:
            try:
                created = orch.run_background_compression(space_id=sid, force_top=req.force_top)
            except Exception as e:  # keep compressing the other spaces
                by_space[sid] = {"error": f"{type(e).__name__}: {e}"}
                continue
            created_ids += [n.id for n in created]
            by_space[sid] = {"count": len(created)}
        return {"created_node_ids": created_ids, "count": len(created_ids), "by_space": by_space}

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

    @app.get("/memory/spaces/manage")
    def manage_spaces(storage: Storage = Depends(get_storage), personas: PersonaRegistry = Depends(get_personas)):
        """Spaces with their node counts and the personas whose allow_spaces include them."""
        counts = storage.stats()["by_space"]
        all_personas = personas.all()
        names = set(counts) | {"personal"}
        for p in all_personas:
            names.update(p.memory_filter.allow_spaces)
        return [
            {
                "space_id": name,
                "nodes": counts.get(name, 0),
                "personas": [p.persona_id for p in all_personas if name in p.memory_filter.allow_spaces],
            }
            for name in sorted(n for n in names if n)
        ]

    @app.patch("/memory/spaces/{space_id}")
    def rename_space(space_id: str, req: SpaceRename, storage: Storage = Depends(get_storage)):
        new_name = req.new_name.strip()
        if not new_name or len(new_name) > 64 or "/" in new_name or new_name == space_id:
            raise HTTPException(400, "Некорректное имя пространства")
        moved = storage.rename_space(space_id, new_name)
        if moved == 0:
            raise HTTPException(404, f"Пространство «{space_id}» не содержит узлов")
        return {"from": space_id, "to": new_name, "nodes_moved": moved}

    @app.delete("/memory/spaces/{space_id}")
    def delete_space(space_id: str, confirm: str = Query(...), storage: Storage = Depends(get_storage)):
        if confirm != space_id:
            raise HTTPException(400, "Подтверждение не совпадает с именем пространства")
        removed = storage.delete_space(space_id)
        return {"space_id": space_id, "nodes_deleted": removed}
