"""Memory and skill lookup tools: fragments, archive search, timeframes."""

from __future__ import annotations

from datetime import datetime, timezone

from ..time_utils import calculate_time_delta as _calculate_time_delta


def _iso_to_ts(s, end_of_day=False):
    """ISO date/datetime -> epoch seconds. A bare date used as an UPPER bound
    (end_of_day=True) means "through the end of that day" — otherwise
    date_to="2026-10-03" silently excluded everything that happened on the 3rd."""
    if not s:
        return None
    s = str(s).strip()
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ts = dt.timestamp()
    if end_of_day and len(s) <= 10:
        ts += 86399.999
    return ts


class MemoryToolsMixin:
    """Memory and skill lookup tools: fragments, archive search, timeframes."""

    def _tool_get_skill(self, args, persona):
        skill_id = str(args.get("skill_id", "")).strip()
        allowed = list(getattr(persona, "active_skill_ids", None) or [])
        if self.skills is None or skill_id not in allowed:
            return {"found": False, "skill_id": skill_id, "available": allowed}
        text = self.skills.fetch_full_text(skill_id)
        if text is None:
            return {"found": False, "skill_id": skill_id, "available": allowed}
        return {"found": True, "skill_id": skill_id, "instructions": text}

    def _tool_get_fragment(self, args, persona):
        node_id = args["id"]
        node = self.storage.get_fragment(node_id)
        if node is None:
            self.storage.log_retrieval("get_fragment", args, [])
            return {"found": False, "id": node_id}
        if persona and not persona.memory_filter.permits(node.space_id, node.tags):
            self.storage.log_retrieval("get_fragment", args, [])
            return {"found": False, "id": node_id, "reason": "access_denied_by_persona_filter"}
        self.storage.log_retrieval("get_fragment", args, [node_id])
        return {
            "found": True,
            "id": node.id,
            "space_id": node.space_id,
            "level": node.level,
            "type": node.type.value if hasattr(node.type, "value") else node.type,
            "summary_text": node.summary_text,
            "raw_content": node.raw_content,
            "occurred_at": node.occurred_at,
            "tags": node.tags,
        }

    def _tool_search_archive(self, args, persona):
        query = args["query"]
        space_id = args.get("space_id")
        date_from = _iso_to_ts(args.get("date_from"))
        date_to = _iso_to_ts(args.get("date_to"), end_of_day=True)
        limit = max(1, min(int(args.get("limit", 10) or 10), 50))
        results = self.storage.search_archive(
            query, space_id=space_id, date_from=date_from, date_to=date_to, limit=limit * 3
        )
        if persona:
            # filter by space AND tags (e.g. a persona that hides "diary")
            results = [r for r in results if persona.memory_filter.permits(r["space_id"], r.get("tags") or [])]
        results = results[:limit]
        for r in results:
            r.pop("tags", None)
        self.storage.log_retrieval("search_archive", args, [r["id"] for r in results])
        return {"results": results}

    def _tool_calculate_time_delta(self, args, persona):
        result = _calculate_time_delta(args["date1"], args["date2"], reference_now=self.reference_now)
        self.storage.log_retrieval("calculate_time_delta", args, [])
        return result

    def _tool_list_fragments_by_timeframe(self, args, persona):
        date_from = _iso_to_ts(args["date_from"])
        date_to = _iso_to_ts(args["date_to"], end_of_day=True)
        space_id = args.get("space_id")
        nodes = self.storage.list_fragments_by_timeframe(date_from, date_to, space_id=space_id)
        if persona:
            nodes = [n for n in nodes if persona.memory_filter.permits(n.space_id, n.tags)]
        self.storage.log_retrieval("list_fragments_by_timeframe", args, [n.id for n in nodes])
        return {
            "results": [
                {"id": n.id, "space_id": n.space_id, "occurred_at": n.occurred_at, "summary_text": n.summary_text}
                for n in nodes
            ]
        }
