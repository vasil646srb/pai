import pytest

from pai.llm import MockLLM
from pai.models import NodeStatus
from pai.storage import Storage
from pai.tree import TreeBuilder


@pytest.fixture
def env(tmp_path):
    storage = Storage(tmp_path / "data")
    llm = MockLLM()
    tree = TreeBuilder(storage, llm, group_size=3)
    yield storage, llm, tree
    storage.close()


def test_ingest_raw_creates_level0_node(env):
    storage, llm, tree = env
    node = tree.ingest_raw("привет мир", space_id="personal")
    assert node.level == 0
    assert node.raw_content == "привет мир"
    fetched = storage.get_fragment(node.id, record_retrieval=False)
    assert fetched is not None


def test_compress_level_waits_for_full_group_by_default(env):
    storage, llm, tree = env
    tree.ingest_raw("a")
    tree.ingest_raw("b")  # only 2, group_size=3 -> not enough

    created = tree.compress_level(0)
    assert created == []


def test_compress_level_creates_summary_on_full_group(env):
    storage, llm, tree = env
    tree.ingest_raw("сообщение один")
    tree.ingest_raw("сообщение два")
    tree.ingest_raw("сообщение три")

    created = tree.compress_level(0)
    assert len(created) == 1
    summary = created[0]
    assert summary.level == 1
    assert len(summary.child_ids) == 3

    # children now have a parent
    for cid in summary.child_ids:
        child = storage.get_fragment(cid, record_retrieval=False)
        assert child.parent_id == summary.id


def test_compress_level_force_compresses_partial_group(env):
    storage, llm, tree = env
    tree.ingest_raw("a")
    tree.ingest_raw("b")  # only 2 of 3

    created = tree.compress_level(0, force=True)
    assert len(created) == 1
    assert len(created[0].child_ids) == 2


def test_summary_mentions_fragment_ids(env):
    storage, llm, tree = env
    n1 = tree.ingest_raw("один")
    n2 = tree.ingest_raw("два")
    n3 = tree.ingest_raw("три")
    created = tree.compress_level(0)
    summary_text = created[0].summary_text
    assert n1.id in summary_text
    assert n2.id in summary_text
    assert n3.id in summary_text


def test_get_roots_after_compression_only_shows_summary(env):
    storage, llm, tree = env
    for i in range(3):
        tree.ingest_raw(f"msg {i}")
    tree.compress_level(0)

    roots = storage.get_roots()
    assert len(roots) == 1
    assert roots[0].level == 1


def test_compress_all_levels_reduces_to_single_top(env):
    storage, llm, tree = env
    for i in range(10):
        tree.ingest_raw(f"событие номер {i}")

    tree.compress_all_levels(force_top=True)
    roots = [r for r in storage.get_roots() if r.status in (NodeStatus.ACTIVE, NodeStatus.PINNED)]
    assert len(roots) == 1


def test_get_tree_tops_orders_by_level_desc(env):
    storage, llm, tree = env
    for i in range(6):
        tree.ingest_raw(f"m{i}")
    tree.compress_all_levels(force_top=False)
    tops = tree.get_tree_tops()
    levels = [t.level for t in tops]
    assert levels == sorted(levels, reverse=True)


def test_spaces_are_not_mixed_during_compression(env):
    storage, llm, tree = env
    for i in range(3):
        tree.ingest_raw(f"work msg {i}", space_id="work")
    for i in range(3):
        tree.ingest_raw(f"personal msg {i}", space_id="personal")

    created_work = tree.compress_level(0, space_id="work")
    created_personal = tree.compress_level(0, space_id="personal")

    assert len(created_work) == 1
    assert len(created_personal) == 1
    assert created_work[0].space_id == "work"
    assert created_personal[0].space_id == "personal"
    # no cross-space child leakage
    work_children = set(created_work[0].child_ids)
    personal_children = set(created_personal[0].child_ids)
    assert work_children.isdisjoint(personal_children)


def test_compression_ratio_reports_by_level(env):
    storage, llm, tree = env
    for i in range(3):
        tree.ingest_raw(f"m{i}")
    tree.compress_level(0)
    ratio = tree.compression_ratio()
    assert ratio["by_level"]["0"] == 3 if "0" in ratio["by_level"] else ratio["by_level"][0] == 3


def test_compress_level_leaves_group_unparented_on_empty_summary(env):
    storage, llm, tree = env
    tree.ingest_raw("один")
    tree.ingest_raw("два")
    tree.ingest_raw("три")

    # Force the summarizer to return nothing usable, twice (initial +
    # retry), simulating e.g. reasoning tokens exhausting the response
    # budget on the provider side.
    llm.queue_response(content="")
    llm.queue_response(content="")

    created = tree.compress_level(0)
    assert created == []

    roots = storage.get_roots()
    # No summary node was fabricated — all three raw nodes remain
    # unparented and will be retried on the next compression pass.
    assert len(roots) == 3
    assert all(r.parent_id is None for r in roots)


def test_compress_level_recovers_after_transient_empty_summary(env):
    storage, llm, tree = env
    tree.ingest_raw("один")
    tree.ingest_raw("два")
    tree.ingest_raw("три")

    # First attempt empty, retry succeeds.
    llm.queue_response(content="")
    llm.queue_response(content="Summary of the three messages.")

    created = tree.compress_level(0)
    assert len(created) == 1
    assert created[0].summary_text.startswith("Summary of the three messages.")
