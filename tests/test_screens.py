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
        if re.search(r'(UPDATE|INSERT INTO) wardrobe_items|(_insert|_save_by_request)\(c, "wardrobe_items"'
                     r'|create_wardrobe_item\(', src):
            writers.add(name)
    assert {"create_wardrobe_items", "deactivate_item", "buy_candidate", "add_starter_wardrobe"} <= writers
    assert writers <= WARDROBE_CHANGES, writers - WARDROBE_CHANGES


def test_each_watched_function_really_changes_wardrobe_and_clears(tmp_path, monkeypatch):
    """코드 문자열이 아니라 실제 상태로: WARDROBE_CHANGES 의 함수를 모두 실제 SQLite 저장소에 불러, 부를 때마다
    옷장(또는 체질)이 실제로 바뀌고 '추천 비우기'가 불리는지 본다. 목록에 함수를 더하면 여기도 더해야 통과한다."""
    import json
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "w.sqlite3")
    db.init_db()
    uid = db.create_user("나", "male", 0, 0)
    cleared = []
    store = WatchedStore(db, lambda: cleared.append(1))

    def snapshot():
        with db.conn() as c:
            return json.dumps([[dict(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY id")] for t in
                               ("wardrobe_items", "users")], default=str)

    cid = db.create_candidate(uid, "후보 코트", "아우터", "코트", "beige", 4, 0)
    steps = [("add_starter_wardrobe", lambda: store.add_starter_wardrobe(uid)),
             ("create_wardrobe_item", lambda: store.create_wardrobe_item(uid, "새 셔츠", "상의", "셔츠", "white", 2, 0)),
             ("create_wardrobe_items", lambda: store.create_wardrobe_items(uid, [
                 {"name": "새 니트", "category": "상의", "subcategory": "니트", "warmth": 3, "rain_ok": 0}])),
             ("update_wardrobe_item", lambda: store.update_wardrobe_item(iid(), uid, "고친 셔츠", "상의", "셔츠", "black", 3, 1)),
             ("deactivate_item", lambda: store.deactivate_item(iid(), uid)),
             ("reactivate_item", lambda: store.reactivate_item(iid(), uid)),
             ("update_item_seasons", lambda: store.update_item_seasons(iid(), uid, ["여름"])),
             ("set_items_active", lambda: store.set_items_active([iid()], uid, False)),
             ("soft_delete_item", lambda: store.soft_delete_item(iid(), uid)),
             ("buy_candidate", lambda: store.buy_candidate(cid, uid)),
             ("update_user", lambda: store.update_user(uid, "female", 0.6, 0.1))]

    def iid():
        with db.conn() as c:
            return c.execute("SELECT id FROM wardrobe_items WHERE name IN ('새 셔츠', '고친 셔츠')").fetchone()[0]

    for name, call in steps:
        before, n = snapshot(), len(cleared)
        call()
        assert snapshot() != before, f"{name}: 옷장·체질이 안 바뀜"
        assert len(cleared) == n + 1, f"{name}: 추천 비우기가 안 불림"
    assert {name for name, _ in steps} == WARDROBE_CHANGES



def test_recommendation_request_key_covers_whole_stored_body():
    """재시도 판단 키: 옷 id·점수가 같아도 옷 이름·이유·근거·날씨가 다르면 다른 요청."""
    import copy

    from screens.recommend import request_key
    fields = ("등교", 12.0, 11.0, 60.0, 0.0, False, 8.0, "manual")
    base = [{"score": 0.9, "pieces": [{"id": 1, "name": "셔츠"}], "reasons": ["이유"], "components": {"weather": 0.9}}]
    k = request_key(fields, base)
    assert request_key(fields, copy.deepcopy(base)) == k
    for change in (lambda r: r[0]["pieces"][0].update(name="블라우스"), lambda r: r[0]["reasons"].append("저녁 안내"),
                   lambda r: r[0]["components"].update(weather=0.8), lambda r: r[0].update(score=0.91)):
        changed = copy.deepcopy(base)
        change(changed)
        assert request_key(fields, changed) != k
    assert request_key(("운동",) + fields[1:], base) != k
