"""Прогресс индексации (с подменным часами) и обзор данных (Storage напрямую, без HTTP)."""
import pytest

from pai.semantic.progress import IndexProgress
from pai.storage import Storage
from pai.web.routes.data import item_detail, list_items, overview


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_progress_rate_eta_percent():
    c = Clock()
    p = IndexProgress(clock=c)
    p.start(100)
    p.add_done(10)
    c.t += 10
    p.add_done(10)
    s = p.snapshot()
    assert s["state"] == "running" and s["total"] == 100 and s["done"] == 20
    assert s["percent"] == 20
    assert s["rate_per_sec"] == 2.0
    assert s["eta_sec"] == 40


def test_progress_old_events_leave_the_window():
    c = Clock()
    p = IndexProgress(clock=c)
    p.start(10)
    p.add_done(5)
    c.t += 120
    s = p.snapshot()
    assert s["rate_per_sec"] == 0.0
    assert s["eta_sec"] is None


def test_progress_add_total_and_finish():
    c = Clock()
    p = IndexProgress(clock=c)
    p.add_total(4)
    assert p.snapshot()["state"] == "running"
    p.add_done(4)
    assert p.snapshot()["percent"] == 100
    p.finish()
    assert p.snapshot()["state"] == "idle"


@pytest.fixture
def store(tmp_path):
    s = Storage(tmp_path / "d")
    yield s
    s.close()


def test_overview_and_listing_without_index(store):
    store.add_request("первый запрос")
    store.add_request("100% готово")
    store.add_inbox("g1", "Анна", "привет с сайта")
    ov = overview(store)
    assert ov["kinds"]["request"] == 2 and ov["kinds"]["inbox"] == 1
    assert ov["index_model"] is None

    page = list_items(store, "request", limit=10)
    assert page["total"] == 2 and len(page["items"]) == 2
    assert page["items"][0]["indexed"] is None


def test_search_treats_percent_and_underscore_literally(store):
    store.add_request("100% готово")
    store.add_request("a_b")
    assert list_items(store, "request", q="100%")["total"] == 1
    assert list_items(store, "request", q="1000")["total"] == 0
    assert list_items(store, "request", q="a_b")["total"] == 1
    assert list_items(store, "request", q="aXb")["total"] == 0


def test_space_filter_for_inbox_and_requests(store):
    store.add_inbox("g1", "Анна", "текст")
    store.add_request("запрос")
    assert list_items(store, "inbox", space_id="inbox")["total"] == 1
    assert list_items(store, "inbox", space_id="requests")["total"] == 0
    assert list_items(store, "request", space_id="requests")["total"] == 1


def test_item_detail_and_not_found(store):
    rid = store.add_request("текст запроса")["id"]
    d = item_detail(store, "request", rid)
    assert d["text"] == "текст запроса" and d["done"] is False
    assert item_detail(store, "request", "нет") is None


def test_bad_arguments_are_rejected(store):
    with pytest.raises(ValueError):
        list_items(store, "xxx")
    with pytest.raises(ValueError):
        list_items(store, "request", indexed="maybe")
