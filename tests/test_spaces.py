import pytest

from pai.models import MemoryNode
from pai.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(tmp_path / "data")
    yield s
    s.close()


def _node(storage, space, text):
    return storage.put_node(MemoryNode(space_id=space, raw_content=text, summary_text=text))


def test_rename_space_moves_nodes(storage):
    a = _node(storage, "work", "alpha")
    _node(storage, "work", "beta")
    _node(storage, "personal", "gamma")
    assert storage.rename_space("work", "office") == 2
    assert storage.stats()["by_space"] == {"personal": 1, "office": 2}
    assert storage.get_fragment(a.id, record_retrieval=False).space_id == "office"


def test_delete_space_removes_nodes_and_search_index(storage):
    _node(storage, "work", "uniqueword")
    keep = _node(storage, "personal", "gamma")
    assert storage.delete_space("work") == 1
    assert "work" not in storage.stats()["by_space"]
    assert storage.get_fragment(keep.id, record_retrieval=False) is not None
    assert storage.search_archive("uniqueword", space_id=None, limit=5) == []


def test_delete_survives_wal_rebuild(storage):
    _node(storage, "work", "alpha")
    _node(storage, "personal", "gamma")
    storage.delete_space("work")
    assert storage.rebuild_index_from_wal() == 1
    assert storage.stats()["by_space"] == {"personal": 1}
