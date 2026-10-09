import tempfile
from pathlib import Path

import pytest

from pai.models import MemoryNode, NodeStatus, NodeType
from pai.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(tmp_path / "data")
    yield s
    s.close()


def test_put_and_get_fragment(storage):
    node = MemoryNode(space_id="personal", raw_content="hello", summary_text="hello")
    storage.put_node(node)
    fetched = storage.get_fragment(node.id)
    assert fetched is not None
    assert fetched.raw_content == "hello"
    assert fetched.retrieval_count == 1  # incremented on read


def test_get_fragment_missing_returns_none(storage):
    assert storage.get_fragment("does-not-exist") is None


def test_position_in_history_auto_assigned_and_monotonic(storage):
    n1 = storage.put_node(MemoryNode(raw_content="a"))
    n2 = storage.put_node(MemoryNode(raw_content="b"))
    assert n2.position_in_history > n1.position_in_history


def test_wal_append_only_and_persists(tmp_path):
    s1 = Storage(tmp_path / "data")
    n = s1.put_node(MemoryNode(raw_content="persisted"))
    s1.close()

    assert (tmp_path / "data" / "wal.jsonl").exists()
    contents = (tmp_path / "data" / "wal.jsonl").read_text()
    assert n.id in contents


def test_rebuild_index_from_wal(tmp_path):
    s1 = Storage(tmp_path / "data")
    n1 = s1.put_node(MemoryNode(raw_content="a", space_id="work"))
    n2 = s1.put_node(MemoryNode(raw_content="b", space_id="personal"))
    s1.close()

    # Simulate index corruption/loss by rebuilding from WAL into a fresh connection
    s2 = Storage(tmp_path / "data")
    replayed = s2.rebuild_index_from_wal()
    assert replayed == 2
    assert s2.get_fragment(n1.id, record_retrieval=False).raw_content == "a"
    assert s2.get_fragment(n2.id, record_retrieval=False).raw_content == "b"
    s2.close()


def test_get_by_level_and_space_filter(storage):
    storage.put_node(MemoryNode(raw_content="a", level=0, space_id="work"))
    storage.put_node(MemoryNode(raw_content="b", level=0, space_id="personal"))
    storage.put_node(MemoryNode(raw_content="c", level=1, space_id="work"))

    work_l0 = storage.get_by_level(0, space_id="work")
    assert len(work_l0) == 1
    assert work_l0[0].raw_content == "a"


def test_get_roots_only_parentless(storage):
    parent = storage.put_node(MemoryNode(raw_content="parent", level=1))
    child = MemoryNode(raw_content="child", level=0, parent_id=parent.id)
    storage.put_node(child)

    roots = storage.get_roots()
    ids = [r.id for r in roots]
    assert parent.id in ids
    assert child.id not in ids


def test_search_archive_finds_text(storage):
    storage.put_node(MemoryNode(raw_content="обсуждали архитектуру базы данных SQLite", summary_text="db"))
    storage.put_node(MemoryNode(raw_content="покупка молока в магазине", summary_text="groceries"))

    results = storage.search_archive("SQLite")
    assert len(results) == 1
    assert "SQLite" in results[0]["snippet"] or "sqlite" in results[0]["snippet"].lower()


def test_search_archive_empty_query_returns_empty(storage):
    assert storage.search_archive("   ") == []


def test_search_archive_respects_space_filter(storage):
    storage.put_node(MemoryNode(raw_content="общий текст про проект", space_id="work"))
    storage.put_node(MemoryNode(raw_content="общий текст про проект", space_id="personal"))

    results = storage.search_archive("проект", space_id="work")
    assert all(r["space_id"] == "work" for r in results)


def test_search_archive_excludes_hidden(storage):
    n = storage.put_node(MemoryNode(raw_content="секретная информация про финансы"))
    storage.set_status(n.id, NodeStatus.HIDDEN)
    results = storage.search_archive("секретная")
    assert results == []


def test_list_fragments_by_timeframe(storage):
    storage.put_node(MemoryNode(raw_content="old", occurred_at=1000.0))
    storage.put_node(MemoryNode(raw_content="mid", occurred_at=2000.0))
    storage.put_node(MemoryNode(raw_content="new", occurred_at=3000.0))

    results = storage.list_fragments_by_timeframe(1500.0, 2500.0)
    assert len(results) == 1
    assert results[0].raw_content == "mid"


def test_retrieval_log(storage):
    seq = storage.log_retrieval("get_fragment", {"id": "abc"}, ["abc"])
    assert isinstance(seq, int)
    storage.mark_used_in_final_answer(seq)
    log = storage.get_retrieval_log()
    assert log[0]["used_in_final_answer"] == 1


def test_stats(storage):
    storage.put_node(MemoryNode(raw_content="a", level=0, space_id="work"))
    storage.put_node(MemoryNode(raw_content="b", level=1, space_id="personal"))
    stats = storage.stats()
    assert stats["total_nodes"] == 2
    assert stats["by_level"][0] == 1
    assert stats["by_level"][1] == 1
    assert stats["by_space"]["work"] == 1


def test_set_status_updates_node(storage):
    n = storage.put_node(MemoryNode(raw_content="x"))
    storage.set_status(n.id, NodeStatus.ARCHIVED)
    fetched = storage.get_fragment(n.id, record_retrieval=False)
    assert fetched.status == NodeStatus.ARCHIVED


# ---------- export / import (backup & restore) ----------

def test_export_wal_roundtrip(storage, tmp_path):
    storage.put_node(MemoryNode(raw_content="a"))
    storage.put_node(MemoryNode(raw_content="b"))
    data = storage.export_wal()
    assert data == storage.wal_path.read_bytes()

    restored = Storage(tmp_path / "restored")
    count = restored.import_wal(data, mode="replace")
    assert count == 2
    assert restored.count_nodes() == 2
    restored.close()


def test_export_empty_store_returns_empty_bytes(tmp_path):
    s = Storage(tmp_path / "empty")
    assert s.export_wal() == b""
    s.close()


def test_import_merge_keeps_existing_and_adds_new(storage, tmp_path):
    existing = storage.put_node(MemoryNode(raw_content="existing"))

    other = Storage(tmp_path / "other")
    incoming = other.put_node(MemoryNode(raw_content="incoming"))
    backup = other.export_wal()
    other.close()

    count = storage.import_wal(backup, mode="merge")
    assert count == 2
    assert storage.get_fragment(existing.id, record_retrieval=False) is not None
    assert storage.get_fragment(incoming.id, record_retrieval=False) is not None


def test_import_replace_discards_existing(storage, tmp_path):
    old = storage.put_node(MemoryNode(raw_content="old"))

    other = Storage(tmp_path / "other")
    other.put_node(MemoryNode(raw_content="fresh"))
    backup = other.export_wal()
    other.close()

    count = storage.import_wal(backup, mode="replace")
    assert count == 1
    assert storage.get_fragment(old.id, record_retrieval=False) is None


def test_import_rejects_invalid_json(storage):
    with pytest.raises(ValueError):
        storage.import_wal(b"not json at all\n")


def test_import_rejects_malformed_record(storage):
    with pytest.raises(ValueError):
        storage.import_wal(b'{"op": "put"}\n')  # missing "node"


def test_import_rejects_bad_mode(storage):
    with pytest.raises(ValueError):
        storage.import_wal(b"", mode="bogus")


def test_import_bad_file_leaves_store_untouched(storage):
    storage.put_node(MemoryNode(raw_content="safe"))
    before = storage.count_nodes()
    with pytest.raises(ValueError):
        storage.import_wal(b'{"op": "put", "node": {}}\nnot json\n')
    assert storage.count_nodes() == before
