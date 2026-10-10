"""Layer 1 — RAPTOR-like hierarchical memory tree.

Only dialogue history is compressed this way; system instructions use a
separate modular scheme (see skills.py). Compression is recursive:
level 0 (raw) -> level 1 (primary summaries) -> level 2 (thematic) -> ...
giving logarithmic growth of live context relative to linear history growth.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .llm import LLMClient
from .models import MemoryNode, NodeStatus, NodeType, now_ts
from .prompts import load_prompt
from .storage import Storage
from .usage import estimate_tokens

_log = logging.getLogger(__name__)

MAX_FRAGMENT_CHARS_FOR_SUMMARY = 3000


class TreeBuilder:
    def __init__(self, storage: Storage, llm: LLMClient, group_size: int = 5):
        """`group_size` = how many level-N nodes get folded into one level-(N+1) summary."""
        self.storage = storage
        self.llm = llm
        self.group_size = group_size
        # Set by Orchestrator so compression calls show up in Usage too.
        self.usage = None

    # ---------- Level 0 ingestion ----------

    def ingest_raw(
        self,
        content: str,
        space_id: str = "personal",
        node_type: NodeType = NodeType.TEXT,
        occurred_at: float | None = None,
        tags: list[str] | None = None,
        annotations: list | None = None,
        reasoning: str | None = None,
    ) -> MemoryNode:
        node = MemoryNode(
            space_id=space_id,
            level=0,
            type=node_type,
            summary_text=content[:200],
            raw_content=content,
            occurred_at=occurred_at if occurred_at is not None else now_ts(),
            tags=tags or [],
            annotations=annotations or [],
            status=NodeStatus.ACTIVE,
            reasoning=reasoning or None,
        )
        return self.storage.put_node(node)

    # ---------- Recursive compression ----------

    def _format_group_for_summary(self, nodes: list[MemoryNode]) -> str:
        lines = []
        for n in nodes:
            ts = ""
            if n.occurred_at:
                ts = datetime.fromtimestamp(n.occurred_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            body = n.summary_text if n.level > 0 else n.raw_content
            if len(body) > MAX_FRAGMENT_CHARS_FOR_SUMMARY:
                # The summary is only a table of contents (exact text stays
                # reachable via get_fragment). Unbounded fragments — e.g. a
                # 200k-char web_fetch result — would overflow the context
                # window and make compression fail forever.
                body = body[:MAX_FRAGMENT_CHARS_FOR_SUMMARY] + f"… [truncated, {len(body)} chars total]"
            lines.append(f"[{ts}] (id={n.id}) {body}")
        return "\n".join(lines)

    def _summary_instruction(self) -> str:
        return (
            load_prompt("summary_instruction")
        )

    def compress_level(
        self, level: int, space_id: str = "personal", force: bool = False
    ) -> list[MemoryNode]:
        """Group unpromoted nodes at `level` into new level+1 summary nodes.

        A node counts as "unpromoted" if it has no parent yet. Groups are formed
        in chronological (position_in_history) order, `group_size` at a time.
        Only full groups are compressed unless `force=True` (compress remainder too).
        """
        candidates = [
            n for n in self.storage.get_by_level(level, space_id=space_id, statuses=["active", "pinned"])
            if n.parent_id is None
        ]
        candidates.sort(key=lambda n: n.position_in_history)

        new_nodes: list[MemoryNode] = []
        for i in range(0, len(candidates), self.group_size):
            group = candidates[i:i + self.group_size]
            if len(group) < self.group_size and not force:
                continue  # wait for a full group unless forced (e.g. end-of-session flush)
            if not group:
                continue
            summary_node = self._summarize_group(group, level + 1, space_id)
            if summary_node is not None:
                new_nodes.append(summary_node)
            # else: summarization failed for this group (see _summarize_group)
            # — its members stay parentless and will be retried on the next
            # compression pass instead of being silently promoted with a
            # blank summary.
        return new_nodes

    def _summarize_group(self, group: list[MemoryNode], new_level: int, space_id: str) -> MemoryNode | None:
        from .orchestrator.llm_runner import calibrate_breakdown  # lazy: avoids import cycle
        text_block = self._format_group_for_summary(group)
        instruction = self._summary_instruction()
        src_level = new_level - 1
        src_ids = [n.id for n in group]
        # Per-fragment composition: what exactly went into the summarizer.
        fragments = []
        for n in group:
            body = n.summary_text if n.level > 0 else n.raw_content
            fragments.append({"id": n.id, "level": n.level, "chars": len(body or ""),
                              "tokens_est": estimate_tokens(body or ""),
                              "preview": (body or "")[:120]})
        req_breakdown = {
            "parts": [
                {"key": "summary_instruction", "label": "Инструкция суммаризатору", "group": "instructions",
                 "tokens_est": estimate_tokens(instruction), "chars": len(instruction), "meta": {}, "text": instruction},
                {"key": "fragments", "label": f"Фрагменты для сжатия (L{src_level} → L{new_level})", "group": "history",
                 "tokens_est": estimate_tokens(text_block), "chars": len(text_block),
                 "meta": {"count": len(group), "fragments": fragments}, "text": text_block},
            ],
            "total_tokens_est": estimate_tokens(instruction) + estimate_tokens(text_block),
            "chat_history": {"messages_in_tail": 0, "user": 0, "assistant": 0, "tail_length_cfg": None,
                             "extra_messages_in_request": 2},
            "instruction_keys": ["summary_instruction"],
        }
        base_req = {
            "source_node_ids": src_ids, "source_level": src_level, "target_level": new_level,
            "fragments_count": len(group), "fragments": fragments,
            "prompt": instruction, "content": text_block,
            "input_chars": len(text_block), "input_tokens_est": estimate_tokens(text_block),
        }
        summary_text = ""
        for attempt in range(2):  # one retry — covers transient empty/truncated responses
            attempt_req = dict(base_req, attempt=attempt)
            try:
                if hasattr(self.llm, "summarize_ex"):
                    ex = self.llm.summarize_ex(text_block, instruction=instruction)
                    summary_text = ex["summary"]
                else:  # clients without usage info
                    import time as _t
                    _t0 = _t.time()
                    summary_text = self.llm.summarize(text_block, instruction=instruction)
                    ex = {"summary": summary_text, "usage": {}, "model": getattr(self.llm, "model", ""),
                          "context_length": None, "request_params": {}, "latency_seconds": _t.time() - _t0,
                          "reasoning": None}
                u = ex.get("usage") or {}
                bd = calibrate_breakdown(dict(req_breakdown, parts=[dict(p) for p in req_breakdown["parts"]]), u)
                out_tok = int(u.get("completion_tokens") or estimate_tokens(summary_text))
                ctx_tok = int(u.get("prompt_tokens") or bd["total_tokens_est"])
                # Real accounting: compression calls used to be invisible in Usage.
                if self.usage is not None:
                    if u:
                        self.usage.record_api("compression", u, ex.get("latency_seconds") or 0.0,
                                              model=ex.get("model") or "", context_length=ex.get("context_length"))
                    else:
                        self.usage.record("compression", instruction + "\n" + text_block, summary_text, 0,
                                          ex.get("latency_seconds") or 0.0)
                try:
                    self.storage.log_action(
                        "llm_request", ex.get("model") or getattr(self.llm, "model", "") or "llm",
                        request={**(ex.get("request_params") or {}),
                                 "messages": [{"role": "system", "content": instruction},
                                              {"role": "user", "content": text_block}],
                                 "tools": None, "purpose": "compression",
                                 "phase": f"compress L{src_level}→L{new_level}",
                                 "compression": {"source_node_ids": src_ids, "source_level": src_level,
                                                 "target_level": new_level, "fragments_count": len(group),
                                                 "attempt": attempt},
                                 "breakdown": bd},
                        response={
                            "content": summary_text, "reasoning": ex.get("reasoning"), "tool_calls": None,
                            "usage": u,
                            "tokens": {"context": ctx_tok, "completion": out_tok,
                                       "reasoning": int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
                                       "cached": int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                                       "source": "provider" if u else "estimate"},
                            "context_length": ex.get("context_length"),
                            "compression": {
                                "compression_ratio": round(ctx_tok / out_tok, 2) if out_tok else None,
                                "summary_chars": len(summary_text),
                                "empty": not summary_text.strip(),
                            },
                        },
                        space_id=space_id, latency_seconds=ex.get("latency_seconds"),
                    )
                except Exception as log_err:  # event logging must never block compression
                    _log.warning("compression event not recorded: %s", log_err, exc_info=True)
            except Exception as e:  # summarizer failure: retried once, then the group is skipped
                _log.warning("summarizer failed on attempt %s: %s", attempt, e, exc_info=True)
                summary_text = ""
                try:
                    self.storage.log_compression_event(
                        "summary_error", dict(attempt_req), response={"error": str(e)}, space_id=space_id,
                    )
                    self.storage.log_action(
                        "llm_request", getattr(self.llm, "model", "") or "llm",
                        request={"purpose": "compression", "phase": f"compress L{src_level}→L{new_level}",
                                 "messages": [{"role": "system", "content": instruction},
                                              {"role": "user", "content": text_block}], "tools": None,
                                 "compression": {"source_node_ids": src_ids, "source_level": src_level,
                                                 "target_level": new_level, "fragments_count": len(group),
                                                 "attempt": attempt},
                                 "breakdown": calibrate_breakdown(
                                     dict(req_breakdown, parts=[dict(p) for p in req_breakdown["parts"]]), {})},
                        space_id=space_id, error=str(e),
                    )
                except Exception as log_err:  # event logging must never block compression
                    _log.warning("compression event not recorded: %s", log_err, exc_info=True)
            if summary_text.strip():
                break

        if not summary_text.strip():
            try:
                self.storage.log_compression_event(
                    "summary_failed", {"source_node_ids": src_ids, "source_level": src_level},
                    response={"reason": "empty_summary_after_retry"}, space_id=space_id,
                )
            except Exception as log_err:  # event logging must never block compression
                _log.warning("compression event not recorded: %s", log_err, exc_info=True)
            return None

        try:
            self.storage.log_compression_event(
                "summary_result", {"source_node_ids": src_ids, "source_level": src_level, "target_level": new_level},
                response={"summary": summary_text}, space_id=space_id)
        except Exception as log_err:  # event logging must never block compression
            _log.warning("compression event not recorded: %s", log_err, exc_info=True)
        ids_note = ", ".join(n.id for n in group)
        if ids_note not in summary_text:
            summary_text = f"{summary_text}\nExact fragments: {ids_note}."

        timeframe_start = min((n.occurred_at for n in group if n.occurred_at), default=None)
        timeframe_end = max((n.occurred_at for n in group if n.occurred_at), default=None)

        summary_node = MemoryNode(
            space_id=space_id,
            level=new_level,
            type=NodeType.SUMMARY,
            summary_text=summary_text,
            raw_content="",
            child_ids=[n.id for n in group],
            timeframe_start=timeframe_start,
            timeframe_end=timeframe_end,
            occurred_at=timeframe_end,
            status=NodeStatus.ACTIVE,
        )
        self.storage.put_node(summary_node)

        for child in group:
            child.parent_id = summary_node.id
            self.storage.put_node(child, assign_position=False)

        return summary_node

    def compress_all_levels(self, space_id: str = "personal", force_top: bool = False) -> list[MemoryNode]:
        """Run compress_level repeatedly from level 0 upward until no more full
        groups remain. If force_top, forces compression of the topmost remaining
        partial group so there's always a single tree top."""
        created: list[MemoryNode] = []
        level = 0
        max_iterations = 50
        for _ in range(max_iterations):
            max_level_present = self.storage.get_max_level(space_id=space_id)
            if level > max_level_present:
                break
            new_nodes = self.compress_level(level, space_id=space_id, force=False)
            created.extend(new_nodes)
            level += 1

        if force_top:
            # Force-collapse remaining orphan roots into a single top. We
            # always merge the two lowest-level roots together (promoting
            # the pair to one level above the higher of the two), rather
            # than looping level-by-level — that keeps this from spinning
            # up dozens of near-empty levels when roots are scattered
            # thinly across many levels (e.g. under sustained cache
            # pressure with only 1-2 roots per level).
            for _ in range(max_iterations):
                roots = self.storage.get_roots(space_id=space_id)
                roots = [r for r in roots if r.status in (NodeStatus.ACTIVE, NodeStatus.PINNED)]
                if len(roots) <= 1:
                    break
                roots.sort(key=lambda n: (n.level, n.position_in_history))
                lowest_level = roots[0].level
                same_level_roots = [r for r in roots if r.level == lowest_level]
                if len(same_level_roots) >= 2:
                    group = sorted(same_level_roots, key=lambda n: n.position_in_history)
                    summary_node = self._summarize_group(group, lowest_level + 1, space_id)
                    if summary_node is None:
                        # Summarization failed even after the internal retry —
                        # stop instead of retrying the exact same group in a
                        # tight loop; it'll be picked up on the next
                        # compression pass.
                        break
                    created.append(summary_node)
                    continue
                # Only one root at the lowest level: fold it together with
                # the next-lowest root directly, landing one level above
                # that pair's higher level — collapses the tree instead of
                # growing a new near-empty level for every leftover root.
                other = min((r for r in roots if r is not same_level_roots[0]), key=lambda n: n.level)
                pair = sorted([same_level_roots[0], other], key=lambda n: n.position_in_history)
                target_level = max(r.level for r in pair) + 1
                summary_node = self._summarize_group(pair, target_level, space_id)
                if summary_node is None:
                    break
                created.append(summary_node)
        return created

    # ---------- Tree top selection for live context ----------

    def get_tree_tops(
        self, space_id: str = "personal", max_nodes: int = 10, min_level: int = 0,
    ) -> list[MemoryNode]:
        """Parentless active nodes, highest level first. The live prompt uses
        min_level=1 (summaries only): raw level-0 roots are already covered by
        the chat tail and would just be duplicated."""
        roots = self.storage.get_roots(space_id=space_id, min_level=min_level)
        roots = [r for r in roots if r.status in (NodeStatus.ACTIVE, NodeStatus.PINNED)]
        roots.sort(key=lambda n: (-n.level, n.position_in_history))
        return roots[:max_nodes]

    def compression_ratio(self, space_id: str | None = None) -> dict:
        stats = self.storage.stats()
        by_level = stats["by_level"]
        raw_count = by_level.get(0, 0)
        total_count = stats["total_nodes"]
        return {
            "raw_nodes": raw_count,
            "total_nodes": total_count,
            "levels": sorted(by_level.keys()),
            "by_level": by_level,
        }
