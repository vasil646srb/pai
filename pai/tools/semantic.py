"""Инструмент semantic_search: поиск по смыслу по всей базе, с учётом фильтра пространств персоны."""
from __future__ import annotations

from ..semantic.config import effective_min_score
from .errors import ToolError


class SemanticToolsMixin:
    def _tool_semantic_search(self, args, persona):
        index = getattr(self.storage, "semantic", None)
        if index is None:
            raise ToolError("семантический поиск не подключён")
        cfg = index.settings.get()
        if not cfg["enabled"]:
            raise ToolError("семантический поиск выключен в настройках")
        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("пустой запрос")
        limit = max(1, min(int(args.get("limit") or 8), 30))
        results = index.search(
            query, persona=persona, space_id=args.get("space_id") or None, top_k=limit,
            min_score=effective_min_score(cfg["precision"], cfg["min_score"]),
        )
        for r in results:
            r["text"] = r["text"][:600]
            r.pop("tags", None)
        self.storage.log_retrieval("semantic_search", args, [r["source_id"] for r in results])
        return {"results": results, "count": len(results)}
