"""화면 공통 부분(screens/context.py): 옷장을 바꾸면 지난 추천이 저절로 비워지는지, 빠진 저장소 함수가 없는지."""
import inspect
import re

from core import db
from screens.context import WARDROBE_CHANGES, WatchedStore


class _FakeStore:
    def __init__(self):
        self.calls = []

    def deactivate_item(self, item_id, user_id):
        self.calls.append(("deactivate_item", item_id))
        return "ok"

    def list_wardrobe_items(self, user_id, active_only=True):
        return []


def test_watched_store_calls_back_only_after_wardrobe_changes():
    changed = []
    store = WatchedStore(_FakeStore(), lambda: changed.append(1))
    assert store.list_wardrobe_items(1) == [] and changed == []
    assert store.deactivate_item(3, 1) == "ok" and changed == [1]


def test_every_store_function_that_writes_wardrobe_is_watched():
    """core/db.py 에서 옷장 표를 고치는 함수를 찾아, 모두 '추천 비우기' 대상인지 확인한다 (새 함수를 빠뜨리지 않게)."""
    writers = set()
    for name, fn in inspect.getmembers(db, inspect.isfunction):
        if fn.__module__ != "core.db" or name.startswith("_") or name == "init_db":
            continue
        src = inspect.getsource(fn)
        if re.search(r'(UPDATE|INSERT INTO) wardrobe_items|_insert\(c, "wardrobe_items"|create_wardrobe_item\(', src):
            writers.add(name)
    assert {"create_wardrobe_items", "deactivate_item", "buy_candidate", "add_starter_wardrobe"} <= writers
    assert writers <= WARDROBE_CHANGES, writers - WARDROBE_CHANGES
