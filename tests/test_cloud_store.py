"""공개 배포용 저장소(core/cloud_store.py)가 로컬 SQLite 판(core/db.py)과 같은 결과를 내는지,
다른 계정의 데이터가 섞이지 않는지 확인한다 (가짜 Supabase 사용)."""
import pytest

from core import db
from core.cloud_store import CloudStore
from core.recommender import outfit_key, recommend
from tests.fake_supabase import FakeAPIError, FakeBackend, signed_in_client

REQ, REQ2 = "6f1c1e0a-3a52-4c63-9a0e-2f1d1a0b9c01", "6f1c1e0a-3a52-4c63-9a0e-2f1d1a0b9c02"
DROP = {"id", "user_id", "created_at", "started_at", "completed_at", "saved_at", "updated_at", "session_id",
        "recommendation_id", "edited_at",
        "request_id"}   # Supabase 판은 요청 id 없이 넣은 행에도 이번 저장에서만 쓰는 id 를 붙인다 (값 비교 대상 아님)


def _clean(rows):
    return [{k: v for k, v in r.items() if k not in DROP} for r in rows]


def _scenario(store, uid):
    """앱에서 일어나는 일을 차례로: 스타터 옷장 → 정리 → 추천·저장·피드백 → 구매 후보 → 사기."""
    out = {}
    out["starter_added"] = store.add_starter_wardrobe(uid)
    out["starter_again"] = store.add_starter_wardrobe(uid)
    items = store.list_wardrobe_items(uid)
    first, second = items[0], items[1]
    store.deactivate_item(first["id"], uid)
    store.update_item_seasons(second["id"], uid, ["여름", "겨울"])
    store.set_items_active([items[2]["id"], items[3]["id"]], uid, False)
    store.reactivate_item(items[3]["id"], uid)
    store.soft_delete_item(items[4]["id"], uid)
    store.reactivate_item(items[4]["id"], uid)            # 지운 옷은 다시 켜지지 않아야 함
    # 사진 여러 장 한 번에 저장 → 저장 후 정보 고치기 (지운 옷은 안 바뀜)
    out["batch_empty"] = store.create_wardrobe_items(uid, [])
    new_ids = store.create_wardrobe_items(uid, [
        {"name": "블랙 니트", "category": "상의", "subcategory": "니트", "color": "black", "warmth": 3, "rain_ok": 0,
         "image_path": "u/b0_1.jpg", "notes": "목이 넓음", "ai_category": "상의", "ai_subcategory": "니트",
         "ai_confidence": 0.81},
        {"name": "블루 청바지", "category": "하의", "subcategory": "청바지", "color": "blue", "warmth": 2,
         "rain_ok": False, "ai_category": "하의", "ai_subcategory": "슬랙스", "ai_confidence": 0.42},
        {"name": "블랙 슬리퍼", "category": "신발", "subcategory": "슬리퍼", "color": "black", "warmth": 1,
         "rain_ok": True, "material": "고무"},
    ])
    out["batch_n"] = len(new_ids)
    store.update_wardrobe_item(new_ids[1], uid, "연청 청바지", "하의", "청바지", "skyblue", 2, True,
                               material="데님", notes="밑단 접어 입기")
    store.soft_delete_item(new_ids[2], uid)
    store.update_wardrobe_item(new_ids[2], uid, "바뀌면 안 됨", "상의", "니트", "red", 5, False)
    out["edited_marked"] = sorted(x["name"] for x in store.list_wardrobe_items(uid, active_only=False)
                                  if x.get("edited_at"))                # '정보 수정'으로 고친 옷만 고친 시각이 있음
    out["inactive"] = sorted(x["name"] for x in store.list_inactive_items(uid))
    out["count"] = store.count_wardrobe_items(uid)
    out["items"] = _clean(store.list_wardrobe_items(uid))

    items = store.list_wardrobe_items(uid)
    rec = recommend(items, 12, 11, 60, 0, False, 8, "등교", 0, 0, 3,
                    exclude_keys=store.disliked_item_sets(uid), seed="t")
    sid = store.create_recommendation_session(uid, "등교", 12, 11, 60, 0, False, 8, "manual")
    rids = [store.save_recommendation(sid, i, r) for i, r in enumerate(rec["results"], 1)]
    store.save_outfit(uid, rids[0])
    store.save_outfit(uid, rids[0])                          # 두 번 눌러도 한 번만
    store.complete_recommendation_session(sid)
    store.upsert_feedback(uid, rids[0], rating="like")
    store.upsert_feedback(uid, rids[0], worn=True, thermal_feedback=-1, satisfaction=4)
    store.upsert_feedback(uid, rids[1], rating="dislike")
    sid2 = store.create_recommendation_session(uid, "운동", 25, 26, 50, 0, False, 5, "manual")  # 추천 없는 세션
    # 화면이 쓰는 한 묶음 저장: 같은 요청 id로 다시 보내면 처음 묶음 그대로, 새 요청은 새 세션
    bundle = store.save_recommendation_session(uid, REQ, "등교", 12, 11, 60, 0, False, 8, "manual", rec["results"])
    out["bundle_n"] = len(bundle[1])
    out["bundle_again_same"] = store.save_recommendation_session(uid, REQ, "등교", 12, 11, 60, 0, False, 8, "manual",
                                                                 rec["results"]) == bundle
    out["bundle_empty_n"] = len(store.save_recommendation_session(uid, REQ2, "외출", 30, 31, 40, 0, True, 3, "manual",
                                                                  [])[1])
    dis = store.disliked_item_sets(uid)
    out["disliked_ok"] = dis == {outfit_key(rec["results"][1]["pieces"])}
    out["sessions"] = store.count_recommendation_sessions(uid)
    out["acceptance"] = store.top3_acceptance_rate(uid)
    out["saved"] = [(s["rank"], s["outfit_text"], s["purpose"]) for s in store.list_saved_outfits(uid)]
    logs = store.export_user_logs(uid)
    out["log_columns"] = list(logs.columns)
    out["log_rows"] = [tuple(None if (isinstance(v, float) and v != v) else v
                             for k, v in r.items() if k not in DROP)
                       for r in logs.to_dict("records")]
    out["kpi"] = store.user_kpi_stats(uid)
    assert sid2

    cid = store.create_candidate(uid, "네이비 플리스", "아우터", "플리스", "navy", 3, 0, link="https://example.com/p/1")
    cid2 = store.create_candidate(uid, "레드 니트", "상의", "니트", "red", 3, 0)
    store.delete_candidate(cid2, uid)
    out["candidates"] = _clean(store.list_candidates(uid))
    new_id = store.buy_candidate(cid, uid)
    out["bought"] = {k: v for k, v in next(x for x in store.list_wardrobe_items(uid) if x["id"] == new_id).items()
                     if k not in DROP}
    out["buy_again"] = store.buy_candidate(cid, uid)
    out["candidates_after"] = store.list_candidates(uid)
    return out


@pytest.mark.parametrize("max_rows", [1000, 2])
def test_cloud_store_matches_sqlite(tmp_path, max_rows):
    """max_rows=2: Supabase 가 한 번에 2줄만 돌려줘도(옷 30벌·추천 로그 여러 줄) 끝까지 나눠 받아 결과가 같다."""
    db.DB_PATH = tmp_path / "parity.sqlite3"
    db.init_db()
    local_uid = db.create_user("동일", "female", 0.6, 0.1)
    local = _scenario(db, local_uid)

    backend = FakeBackend()
    backend.max_rows = max_rows
    client = signed_in_client(backend, "a@example.com")
    cloud = CloudStore(client, client.uid)
    cloud.create_user("동일", "female", 0.6, 0.1)
    remote = _scenario(cloud, client.uid)

    assert local["starter_added"] == 30 and local["starter_again"] == 0
    assert local["batch_empty"] == [] and local["batch_n"] == 3
    edited = next(x for x in local["items"] if x["name"] == "연청 청바지")
    assert (edited["color"], edited["rain_ok"], edited["material"], edited["notes"]) == ("skyblue", 1, "데님", "밑단 접어 입기")
    assert edited["ai_subcategory"] == "슬랙스"                      # AI 기록은 그대로
    assert not any(x["name"] == "바뀌면 안 됨" for x in local["items"])
    assert local["disliked_ok"] and remote["disliked_ok"]
    assert local["bundle_n"] == 3 and local["bundle_again_same"] and local["bundle_empty_n"] == 0
    assert local["edited_marked"] == ["연청 청바지"]
    assert local["sessions"] == 4
    for key in local:
        assert remote[key] == local[key], key
    assert len(local["log_rows"]) == 8 and len(local["items"]) == 29        # 둘 다 한도(2줄)보다 많다
    if max_rows == 2:                                       # 나눠 받지 않으면 2줄만 온다 (가짜 백엔드가 한도를 재현함)
        assert len(client.table("wardrobe_items").select("*").execute().data) == 2


def test_profile_round_trip_and_update():
    client = signed_in_client(FakeBackend(), "p@example.com")
    s = CloudStore(client, client.uid)
    assert s.list_users() == [] and s.get_user(client.uid) is None
    uid = s.create_user("나", "male", 0, 0)
    assert uid == client.uid
    s.update_user(uid, "female", 1.0, 0.0)
    u = s.get_user(uid)
    assert (u["name"], u["gender"], u["cold_sensitivity"]) == ("나", "female", 1.0)
    assert s.list_users() == [u]


def test_two_accounts_are_isolated():
    backend = FakeBackend()
    a = signed_in_client(backend, "a@example.com")
    b = signed_in_client(backend, "b@example.com")
    sa, sb = CloudStore(a, a.uid), CloudStore(b, b.uid)
    sa.create_user("A", "male", 0, 0)
    sb.create_user("B", "female", 0, 0)
    sa.add_starter_wardrobe(a.uid)
    a_item = sa.list_wardrobe_items(a.uid)[0]
    a_cand = sa.create_candidate(a.uid, "후보", "상의", "니트", "red", 3, 0)

    # B 화면에는 A의 데이터가 없다
    assert sb.list_wardrobe_items(b.uid) == [] and sb.list_candidates(b.uid) == []
    assert sb.get_user(a.uid) is None
    # B가 A의 id로 바꾸거나 사려고 해도 아무 일도 안 일어난다
    sb.soft_delete_item(a_item["id"], b.uid)
    sb.update_item_seasons(a_item["id"], b.uid, ["겨울"])
    sb.update_wardrobe_item(a_item["id"], b.uid, "B가 바꾼 이름", "상의", "니트", "red", 5, True)
    assert sb.buy_candidate(a_cand, b.uid) is None
    assert sa.list_wardrobe_items(a.uid)[0]["seasons"] is None
    assert sa.list_wardrobe_items(a.uid)[0]["name"] == a_item["name"]
    assert len(sa.list_wardrobe_items(a.uid)) == 30
    # 앱 코드가 다른 사람 id를 넘기면 바로 거절
    with pytest.raises(PermissionError):
        sb.list_wardrobe_items(a.uid)
    # DB 정책: 남의 user_id로 행을 넣거나, 남의 추천에 저장·피드백을 다는 것도 막힌다
    with pytest.raises(FakeAPIError):
        b.table("wardrobe_items").insert({"user_id": a.uid, "name": "x"}).execute()
    sid = sa.create_recommendation_session(a.uid, "등교", 12, 11, 60, 0, False, 8, "manual")
    rid = sa.save_recommendation(sid, 1, {"score": 0.9, "pieces": [], "reasons": [], "components": {}})
    with pytest.raises(FakeAPIError):
        b.table("saved_outfits").insert({"user_id": b.uid, "recommendation_id": rid, "saved_at": "x"}).execute()
    with pytest.raises(FakeAPIError):
        CloudStore(b, b.uid).save_recommendation(sid, 1, {"score": 0.1, "pieces": [], "reasons": [], "components": {}})


def test_signed_out_client_cannot_read():
    backend = FakeBackend()
    a = signed_in_client(backend, "a@example.com")
    CloudStore(a, a.uid).create_user("A", "male", 0, 0)
    a.auth.sign_out()
    with pytest.raises(FakeAPIError):
        a.table("profiles").select("*").execute()


def _sqlite_item(item_id):
    with db.conn() as c:
        return dict(c.execute("SELECT * FROM wardrobe_items WHERE id=?", (item_id,)).fetchone())


def test_edit_skips_deleted_item(tmp_path):
    """지운 옷의 정보 고치기는 DB 행에도 반영되지 않는다 (SQLite·Supabase 둘 다)."""
    db.DB_PATH = tmp_path / "edit.sqlite3"
    db.init_db()
    client = signed_in_client(FakeBackend(), "e@example.com")
    cloud = CloudStore(client, client.uid)
    cloud.create_user("나", "male", 0, 0)
    local_uid = db.create_user("나", "male", 0, 0)
    for store, uid, raw in (
        (db, local_uid, _sqlite_item),
        (cloud, client.uid, lambda i: client.table("wardrobe_items").select("*").eq("id", i).execute().data[0]),
    ):
        iid = store.create_wardrobe_items(uid, [{"name": "그레이 후드티", "category": "상의", "subcategory": "후드티",
                                                 "color": "gray", "warmth": 3, "rain_ok": 0}])[0]
        store.soft_delete_item(iid, uid)
        store.update_wardrobe_item(iid, uid, "바뀐 이름", "하의", "청바지", "blue", 1, True)
        row = raw(iid)
        assert (row["name"], row["category"], row["deleted"]) == ("그레이 후드티", "상의", 1)


def test_buy_candidate_failure_keeps_candidate_and_retry_adds_one(tmp_path, monkeypatch):
    """'샀어요' 중간에 옷장 저장이 실패하면 후보가 그대로 남고, 다시 누르면 옷은 한 벌만 들어간다 (SQLite·Supabase)."""
    db.DB_PATH = tmp_path / "buy.sqlite3"
    db.init_db()
    client = signed_in_client(FakeBackend(), "b@example.com")
    cloud = CloudStore(client, client.uid)
    cloud.create_user("나", "male", 0, 0)
    local_uid = db.create_user("나", "male", 0, 0)

    real_insert = db._insert

    def broken_insert(c, table, values):
        if table == "wardrobe_items":
            raise RuntimeError("저장 실패")
        return real_insert(c, table, values)

    def broken_rpc():   # DB 함수가 후보를 뺀 뒤 옷장에 넣다가 실패 → 함수 전체가 되돌아감
        client.backend.rpc_faults["buy_candidate"] = "error"

    for store, uid, break_it in ((db, local_uid, lambda: monkeypatch.setattr(db, "_insert", broken_insert)),
                                 (cloud, client.uid, broken_rpc)):
        cid = store.create_candidate(uid, "네이비 플리스", "아우터", "플리스", "navy", 3, 0)
        n_before = len(store.list_wardrobe_items(uid))
        break_it()
        with pytest.raises((RuntimeError, FakeAPIError)):
            store.buy_candidate(cid, uid)
        monkeypatch.undo()
        assert [c["id"] for c in store.list_candidates(uid)] == [cid]          # 후보가 그대로 남음
        assert len(store.list_wardrobe_items(uid)) == n_before                  # 옷은 안 들어감
        new_id = store.buy_candidate(cid, uid)
        assert new_id and store.buy_candidate(cid, uid) is None                 # 다시 눌러도 한 벌만
        assert len(store.list_wardrobe_items(uid)) == n_before + 1
        assert store.list_candidates(uid) == []


def test_buy_candidate_lost_response_then_retry_adds_one():
    """옷장에 넣기까지 끝났는데 응답만 못 받은 경우: 다시 눌러도 후보를 되살려 두 번 넣지 않는다 (예전 방식의 중복 원인)."""
    backend = FakeBackend()
    client = signed_in_client(backend, "lost@example.com")
    store = CloudStore(client, client.uid)
    store.create_user("나", "male", 0, 0)
    cid = store.create_candidate(client.uid, "네이비 플리스", "아우터", "플리스", "navy", 3, 0)
    backend.rpc_faults["buy_candidate"] = "lost"
    with pytest.raises(FakeAPIError):
        store.buy_candidate(cid, client.uid)
    assert store.buy_candidate(cid, client.uid) is None                   # 이미 산 후보
    assert [x["name"] for x in store.list_wardrobe_items(client.uid)] == ["네이비 플리스"]
    assert store.list_candidates(client.uid) == []


def test_recommendation_bundle_is_all_or_nothing_and_retry_safe(tmp_path, monkeypatch):
    """추천 세션과 TOP 3는 함께 저장되거나 함께 안 남는다. 응답을 못 받고 같은 요청을 다시 보내도 세션은 하나 (SQLite·Supabase)."""
    db.DB_PATH = tmp_path / "rec.sqlite3"
    db.init_db()
    local_uid = db.create_user("나", "male", 0, 0)
    backend = FakeBackend()
    client = signed_in_client(backend, "rec@example.com")
    cloud = CloudStore(client, client.uid)
    cloud.create_user("나", "male", 0, 0)
    results = [{"score": 0.9 - i / 10, "pieces": [{"id": i, "name": f"옷{i}"}], "reasons": ["r"],
                "components": {"weather": 0.9}} for i in range(3)]
    args = ("등교", 12, 11, 60, 0, False, 8, "manual")

    real_insert = db._insert
    calls = []

    def insert_fails_on_third_result(c, table, values):
        if table == "recommendations":
            calls.append(1)
            if len(calls) == 3:
                raise RuntimeError("셋째 결과 저장 실패")
        return real_insert(c, table, values)

    for store, uid, break_it in (
            (db, local_uid, lambda: monkeypatch.setattr(db, "_insert", insert_fails_on_third_result)),
            (cloud, client.uid, lambda: backend.rpc_faults.update(save_recommendation_session="error"))):
        break_it()
        with pytest.raises((RuntimeError, FakeAPIError)):
            store.save_recommendation_session(uid, REQ, *args, results)
        monkeypatch.undo()
        assert store.count_recommendation_sessions(uid) == 0 and store.export_user_logs(uid).empty   # 반쪽 기록 없음
        sid, rids = store.save_recommendation_session(uid, REQ, *args, results)
        assert len(rids) == 3 and store.count_recommendation_sessions(uid) == 1

    backend.rpc_faults["save_recommendation_session"] = "lost"               # 저장은 됐는데 응답만 유실
    with pytest.raises(FakeAPIError):
        cloud.save_recommendation_session(client.uid, REQ2, *args, results)
    again = cloud.save_recommendation_session(client.uid, REQ2, *args, results)
    assert len(again[1]) == 3 and cloud.count_recommendation_sessions(client.uid) == 2
    assert len(cloud.export_user_logs(client.uid)) == 6


def _req(n):
    return f"6f1c1e0a-3a52-4c63-9a0e-{n:012d}"


def request_rule_scenario(store, uid):
    """등록 요청 id 규칙: id 없으면 늘 새 행, 있으면 같은 id 행을 지금 값으로 고침(없으면 넣음), 묶음 안 반복은 한 행·나중 값.
    tests/test_rls_sql.py 가 같은 장면을 실제 PostgreSQL 에도 돌린다."""
    shirt = {"name": "셔츠", "category": "상의", "subcategory": "셔츠", "color": "white", "warmth": 2, "rain_ok": 0}
    out = {}
    both = [{**shirt, "name": "A", "request_id": _req(1)}, {**shirt, "name": "B", "request_id": _req(2)}]
    a = store.create_wardrobe_items(uid, both)                                   # 전부 요청 id
    out["uuid_retry"] = store.create_wardrobe_items(uid, both) == a
    nulls = [{**shirt, "name": "N1"}, {**shirt, "name": "N2", "request_id": None}]
    n1, n2 = store.create_wardrobe_items(uid, nulls), store.create_wardrobe_items(uid, nulls)   # 전부 없음
    out["null_new_each_time"] = len(set(n1) | set(n2)) == 4
    mixed = [{**shirt, "name": "M", "request_id": _req(3)}, {**shirt, "name": "M0", "request_id": None}]
    m1, m2 = store.create_wardrobe_items(uid, mixed), store.create_wardrobe_items(uid, mixed)   # 섞임 + 재시도
    out["mixed_retry"] = (m1[0] == m2[0], m1[1] != m2[1])
    edited = store.create_wardrobe_items(uid, [{**shirt, "name": "A(고침)", "color": "black", "request_id": _req(1)}])
    out["edit_after_retry"] = edited == a[:1]
    r = store.create_wardrobe_items(uid, [{**shirt, "name": "R1", "request_id": _req(4)},
                                          {**shirt, "name": "R2", "request_id": _req(4)}, {**shirt, "name": "R3"}])
    out["repeat_in_batch"] = (r[0] == r[1], r[2] not in r[:2])
    out["empty"] = store.create_wardrobe_items(uid, [])
    c1 = store.create_candidate(uid, "후보", "아우터", "코트", "beige", 3, 0, request_id=_req(5))
    c2 = store.create_candidate(uid, "후보(고침)", "아우터", "코트", "beige", 3, 0, link="https://example.com/x",
                                request_id=_req(5))
    out["candidate_edit_same_row"] = c1 == c2
    store.create_candidate(uid, "사진 없는 후보", "상의", "니트", "red", 3, 0)
    store.create_candidate(uid, "사진 없는 후보", "상의", "니트", "red", 3, 0)
    # 등록 성공(응답 유실) → 옷장 목록에서 '정보 수정' → 카드 재시도: 목록에서 고친 내용이 남고 충돌로 알린다
    conflicts = []
    e = store.create_wardrobe_items(uid, [{**shirt, "name": "카드 셔츠", "request_id": _req(6)}])
    store.update_wardrobe_item(e[0], uid, "목록에서 고침", "상의", "셔츠", "navy", 3, 0)
    again = store.create_wardrobe_items(uid, [{**shirt, "name": "카드에서 고침", "request_id": _req(6)}], conflicts=conflicts)
    # 등록 성공(응답 유실) → 목록에서 지움 → 카드 재시도: 되살리지 않고 충돌
    d = store.create_wardrobe_items(uid, [{**shirt, "name": "지운 옷", "request_id": _req(7)}])
    store.soft_delete_item(d[0], uid)
    store.create_wardrobe_items(uid, [{**shirt, "name": "지운 옷(카드)", "request_id": _req(7)}], conflicts=conflicts)
    # 후보 등록 성공(응답 유실) → 지움 → 같은 요청 id 로 다른 후보: 지운 후보를 고치지 않고 충돌 (화면은 새 등록으로)
    k = store.create_candidate(uid, "끝난 후보", "아우터", "코트", "beige", 3, 0, request_id=_req(8))
    store.delete_candidate(k, uid)
    store.create_candidate(uid, "새 후보", "아우터", "코트", "black", 3, 0, request_id=_req(8), conflicts=conflicts)
    out["conflicts"] = (again == e, conflicts == [_req(6), _req(7), _req(8)])
    out["items"] = sorted((x["name"], x["color"]) for x in store.list_wardrobe_items(uid))
    out["candidates"] = sorted((x["name"], x["link"]) for x in store.list_candidates(uid))
    return out


EXPECTED_RULE = {
    "uuid_retry": True, "null_new_each_time": True, "mixed_retry": (True, True), "edit_after_retry": True,
    "repeat_in_batch": (True, True), "empty": [], "candidate_edit_same_row": True, "conflicts": (True, True),
    "items": sorted([("A(고침)", "black"), ("B", "white"), ("N1", "white"), ("N2", "white"), ("N1", "white"),
                     ("N2", "white"), ("M", "white"), ("M0", "white"), ("M0", "white"), ("R2", "white"),
                     ("R3", "white"), ("목록에서 고침", "navy")]),
    "candidates": [("사진 없는 후보", ""), ("사진 없는 후보", ""), ("후보(고침)", "https://example.com/x")],
}


def test_request_id_rule_is_the_same_in_both_stores(tmp_path):
    db.DB_PATH = tmp_path / "rule.sqlite3"
    db.init_db()
    client = signed_in_client(FakeBackend(), "rule@example.com")
    cloud = CloudStore(client, client.uid)
    cloud.create_user("나", "male", 0, 0)
    assert request_rule_scenario(db, db.create_user("나", "male", 0, 0)) == EXPECTED_RULE
    assert request_rule_scenario(cloud, client.uid) == EXPECTED_RULE


def test_batch_save_is_all_or_nothing(tmp_path, monkeypatch):
    """묶음 저장 중간(둘째 행)에 실패하면 앞 행도 남지 않는다 (SQLite 거래, 가짜 백엔드도 요청 하나를 되돌림)."""
    shirt = {"name": "셔츠", "category": "상의", "subcategory": "셔츠", "color": "white", "warmth": 2, "rain_ok": 0}
    batch = [{**shirt, "request_id": _req(1)}, {**shirt, "name": "둘째"}]
    db.DB_PATH = tmp_path / "atomic.sqlite3"
    db.init_db()
    uid = db.create_user("나", "male", 0, 0)
    real, seen = db._insert, []

    def second_fails(c, table, values):
        seen.append(table)
        if table == "wardrobe_items" and seen.count("wardrobe_items") == 2:
            raise RuntimeError("둘째 행 실패")
        return real(c, table, values)
    monkeypatch.setattr(db, "_insert", second_fails)
    with pytest.raises(RuntimeError):
        db.create_wardrobe_items(uid, batch)
    monkeypatch.undo()
    assert db.list_wardrobe_items(uid) == []

    backend = FakeBackend()
    client = signed_in_client(backend, "atomic@example.com")
    cloud = CloudStore(client, client.uid)
    cloud.create_user("나", "male", 0, 0)
    backend.faults[("wardrobe_items", "insert")] = [None, "error"]    # 저장 함수 안에서 첫 행을 넣은 뒤 둘째에서 실패
    with pytest.raises(FakeAPIError):
        cloud.create_wardrobe_items(client.uid, batch)
    assert cloud.list_wardrobe_items(client.uid) == [] and backend.rows("wardrobe_items") == []
    assert len(cloud.create_wardrobe_items(client.uid, batch)) == 2    # 다시 하면 둘 다


def test_photo_with_request_key_is_saved_once(tmp_path):
    from core.media import LocalMedia, SupabaseMedia
    local = LocalMedia(tmp_path)
    assert local.save(7, "b_", b"1", ".jpg", key="k1") == local.save(7, "b_", b"1", ".jpg", key="k1")
    assert len(list(tmp_path.iterdir())) == 1
    backend = FakeBackend()
    c = signed_in_client(backend, "photo@example.com")
    m = SupabaseMedia(c, c.uid)
    from tests.test_deploy_helpers import _png
    backend.faults[("storage", "upload")] = "lost"                      # 올라갔는데 응답만 유실
    with pytest.raises(FakeAPIError):
        m.save(c.uid, "b_", _png(), ".png", key="k2")
    ref = m.save(c.uid, "b_", _png(), ".png", key="k2")                 # 다시 올려도 같은 파일 하나
    assert ref.endswith("/b_k2.jpg") and len(backend.objects) == 1
    m.save(c.uid, "b_", _png(), ".png")
    m.save(c.uid, "b_", _png(), ".png")                                 # 이름을 안 정하면 예전처럼 새 파일
    from core.media import to_cloud_jpeg
    ready = to_cloud_jpeg(_png())
    ref = m.save(c.uid, "b_", ready, ".jpg", key="k3", ready=True)      # 이미 줄인 사진은 그대로 올린다
    assert backend.objects["wardrobe/" + ref[3:]] == ready
    assert len(backend.objects) >= 2


def test_photo_cache_drops_oldest_when_full():
    from core.media import PhotoCache
    cache = PhotoCache(max_bytes=10)
    cache["a"], cache["b"] = b"1234", b"5678"
    assert cache["a"] == b"1234"            # a를 최근에 씀 → b가 가장 오래됨
    cache["c"] = b"9012"
    assert "b" not in cache and "a" in cache and "c" in cache and len(cache) == 2


def _six(monkeypatch, max_rows=1000):
    from core import cloud_store
    monkeypatch.setattr(cloud_store, "PAGE", 3)
    backend = FakeBackend()
    backend.max_rows = max_rows
    client = signed_in_client(backend, "page@example.com")
    store = CloudStore(client, client.uid)
    store.create_user("나", "male", 0, 0)
    store.create_wardrobe_items(client.uid, [{"name": f"옷{i}", "category": "상의", "warmth": 2, "rain_ok": 0}
                                             for i in range(1, 7)])
    return backend, client, store


@pytest.mark.parametrize("change, expected", [
    ("add", [6, 5, 4, 3, 2, 1]),              # 첫 쪽 뒤 새 옷(id 7): 이미 받은 id 위라 이번 목록엔 없고 중복도 없음
    ("delete_fetched", [6, 5, 4, 3, 2, 1]),   # 이미 받은 6을 지워도 3을 건너뛰지 않음 (offset 방식은 3이 빠짐)
    ("delete_unfetched", [6, 5, 4, 3, 1]),    # 아직 안 받은 2를 지우면 2만 없음
    ("deactivate", [6, 5, 4, 3, 2]),          # 조회 조건 변경: 아직 안 받은 1을 넣어 두면 1만 없음
    ("delete_many", [6, 5, 4]),               # 남은 줄보다 많이 지워도 범위 오류(416) 없이 끝남
])
def test_list_paging_is_stable_when_rows_change_between_pages(monkeypatch, change, expected):
    backend, client, store = _six(monkeypatch)
    uid = client.uid
    act = {"add": lambda: store.create_wardrobe_item(uid, "옷7", "상의", None, None, 2, 0),
           "delete_fetched": lambda: store.soft_delete_item(6, uid),
           "delete_unfetched": lambda: store.soft_delete_item(2, uid),
           "deactivate": lambda: store.deactivate_item(1, uid),
           "delete_many": lambda: [store.soft_delete_item(i, uid) for i in (1, 2, 3)]}[change]
    backend.after_select.append(act)
    ids = [x["id"] for x in store.list_wardrobe_items(uid)]
    assert ids == expected and len(ids) == len(set(ids))


def test_paging_with_server_limit_smaller_than_page_and_empty_results(monkeypatch):
    """서버가 한 번에 2줄만 줘도(PAGE 3) 전체 줄 수만큼 이어 받는다. 새 사용자는 모든 목록이 빈 결과."""
    backend, client, store = _six(monkeypatch, max_rows=2)
    backend.calls.clear()
    assert [x["id"] for x in store.list_wardrobe_items(client.uid)] == [6, 5, 4, 3, 2, 1]
    assert backend.calls.count(("wardrobe_items", "select")) == 3
    other = signed_in_client(backend, "empty@example.com")
    so = CloudStore(other, other.uid)
    so.create_user("빈", "male", 0, 0)
    assert so.list_wardrobe_items(other.uid) == [] and so.list_candidates(other.uid) == []
    assert so.list_saved_outfits(other.uid) == [] and so.disliked_item_sets(other.uid) == set()
    assert so.export_user_logs(other.uid).empty


def test_log_export_order_with_sessions_without_results(tmp_path):
    """추천 로그: 세션 최신순, 세션 안은 순위순, 추천이 없는 세션(순위·추천 id 가 NULL)도 한 줄 — SQLite 와 같은 순서."""
    db.DB_PATH = tmp_path / "log.sqlite3"
    db.init_db()
    client = signed_in_client(FakeBackend(), "log@example.com")
    cloud = CloudStore(client, client.uid)
    cloud.create_user("나", "male", 0, 0)
    res = [{"score": 0.9 - i / 10, "pieces": [{"id": i, "name": f"옷{i}"}, {"id": 9, "name": "신발"}], "reasons": [],
            "components": {}} for i in range(3)]
    out = {}
    for name, store, uid in (("local", db, db.create_user("나", "male", 0, 0)), ("cloud", cloud, client.uid)):
        store.save_recommendation_session(uid, _req(1), "등교", 12, 11, 60, 0, False, 8, "manual", res)
        store.save_recommendation_session(uid, _req(2), "운동", 20, 20, 50, 0, False, 5, "manual", [])
        store.save_recommendation_session(uid, _req(3), "외출", 5, 3, 70, 1, True, 9, "manual", res[:2])
        store.save_recommendation_session(uid, _req(4), "격식", 25, 26, 40, 0, False, 2, "manual", [])
        logs = store.export_user_logs(uid)
        out[name] = (list(logs.columns), [(r["purpose"], None if r["rank"] != r["rank"] else r["rank"],
                                           r.get("outfit") if isinstance(r.get("outfit"), str) else None)
                                          for r in logs.to_dict("records")])
    assert out["local"] == out["cloud"]
    assert [x[0] for x in out["local"][1]] == ["격식", "외출", "외출", "운동", "등교", "등교", "등교"]
    assert out["local"][1][1] == ("외출", 1, "옷0 + 신발") and out["local"][1][0][1] is None


def test_dislikes_that_appear_during_paging_are_not_cut_off(monkeypatch):
    """비선호 피드백 id 1,2,3,5,6,7 (PAGE 3)에서 첫 쪽 뒤 4를 '별로'로 바꿔도 7까지 받는다 (첫 쪽 개수로 끝내지 않음)."""
    from core import cloud_store
    monkeypatch.setattr(cloud_store, "PAGE", 3)
    backend = FakeBackend()
    client = signed_in_client(backend, "dis@example.com")
    store = CloudStore(client, client.uid)
    store.create_user("나", "male", 0, 0)
    res = [{"score": 0.9, "pieces": [{"id": i, "name": f"옷{i}"}], "reasons": [], "components": {}} for i in range(1, 8)]
    _, rids = store.save_recommendation_session(client.uid, _req(1), "등교", 12, 11, 60, 0, False, 8, "m", res)
    for i, rid in enumerate(rids, 1):
        store.upsert_feedback(client.uid, rid, rating="like" if i == 4 else "dislike")
    backend.after_select.append(lambda: store.upsert_feedback(client.uid, rids[3], rating="dislike"))
    assert sorted(min(k) for k in store.disliked_item_sets(client.uid)) == [1, 2, 3, 4, 5, 6, 7]


def _csv_without_ids(df):
    return df.drop(columns=["session_id", "started_at", "completed_at", "recommendation_id"]).to_csv(index=False)


def test_log_csv_keeps_float_format_like_sqlite(tmp_path):
    """정수처럼 보이는 실수(22.0, 70.0, 0.0, 점수 1.0)만 있어도 CSV 문자열이 SQLite 판과 같다 (JSON 을 거쳐도 "22.0")."""
    db.DB_PATH = tmp_path / "csv.sqlite3"
    db.init_db()
    client = signed_in_client(FakeBackend(), "csv@example.com")
    cloud = CloudStore(client, client.uid)
    cloud.create_user("나", "male", 0, 0)
    res = [{"score": 1.0, "pieces": [{"id": 1, "name": "셔츠"}], "reasons": [], "components": {}}]
    csv = {}
    for name, store, uid in (("local", db, db.create_user("나", "male", 0, 0)), ("cloud", cloud, client.uid)):
        store.save_recommendation_session(uid, _req(1), "등교", 22.0, 22.0, 70.0, 0.0, False, 8.0, "manual", res)
        store.save_recommendation_session(uid, _req(2), "운동", 18.0, None, 50.0, 0.0, False, 3.0, "manual", [])
        csv[name] = _csv_without_ids(store.export_user_logs(uid))
    assert csv["local"] == csv["cloud"] and ",22.0,22.0,70.0,0.0," in csv["cloud"]


def test_ids_do_not_depend_on_returning_order():
    """요청 id 가 없는 행도 저장할 때 붙이는 id 로 맞추므로, 서버가 넣은 행을 다른 순서로 돌려줘도 id 가 바뀌지 않는다
    (실제 PostgREST 에서 순서가 바뀐 사례를 본 것은 아니다. 순서에 기대지 않게만 했다)."""
    backend = FakeBackend()
    backend.reverse_returning = True
    client = signed_in_client(backend, "order@example.com")
    store = CloudStore(client, client.uid)
    store.create_user("나", "male", 0, 0)
    shirt = {"category": "상의", "warmth": 2, "rain_ok": 0}
    ids = store.create_wardrobe_items(client.uid, [{**shirt, "name": "첫째"}, {**shirt, "name": "둘째", "request_id": _req(1)},
                                                   {**shirt, "name": "셋째"}])
    names = {x["id"]: x["name"] for x in store.list_wardrobe_items(client.uid)}
    assert [names[i] for i in ids] == ["첫째", "둘째", "셋째"]


def test_retry_that_fails_on_second_existing_row_changes_nothing_and_clears_recommendations(tmp_path, monkeypatch):
    """이미 저장된 두 벌을 카드 내용으로 고치는 재시도에서 둘째 고치기가 실패하면, 첫째도 그대로다 (묶음 전체가 한 거래 —
    SQLite·Supabase 같음). 실제로 바뀌었는지 모르는 실패라도 지난 추천은 비운다."""
    from screens.context import WatchedStore
    base = {"category": "상의", "warmth": 2, "rain_ok": 0}
    first = [{**base, "name": "첫째", "request_id": _req(1)}, {**base, "name": "둘째", "request_id": _req(2)}]
    retry = [{**base, "name": "첫째(고침)", "request_id": _req(1)}, {**base, "name": "둘째(고침)", "request_id": _req(2)}]

    db.DB_PATH = tmp_path / "two.sqlite3"
    db.init_db()
    local_uid = db.create_user("나", "male", 0, 0)
    backend = FakeBackend()
    client = signed_in_client(backend, "two@example.com")
    cloud = CloudStore(client, client.uid)
    cloud.create_user("나", "male", 0, 0)
    real_execute, updates = db.sqlite3.Connection.execute, []

    def sqlite_second_update_fails(store_monkeypatch):
        class Conn(db._Conn):
            def execute(self, sql, *a):
                if sql.startswith("UPDATE wardrobe_items SET name"):
                    updates.append(1)
                    if len(updates) == 2:
                        raise RuntimeError("둘째 고치기 실패")
                return real_execute(self, sql, *a)
        store_monkeypatch.setattr(db, "_Conn", Conn)

    for store, uid, break_it in ((db, local_uid, lambda: sqlite_second_update_fails(monkeypatch)),
                                 (cloud, client.uid,
                                  lambda: backend.faults.update({("wardrobe_items", "update"): [None, "error"]}))):
        store.create_wardrobe_items(uid, first)
        cleared = []
        watched = WatchedStore(store, lambda: cleared.append(1))
        break_it()
        with pytest.raises(Exception):
            watched.create_wardrobe_items(uid, retry)
        monkeypatch.undo()
        assert sorted(x["name"] for x in store.list_wardrobe_items(uid)) == ["둘째", "첫째"], store
        assert cleared == [1], store
        assert watched.create_wardrobe_items(uid, retry) and \
            sorted(x["name"] for x in store.list_wardrobe_items(uid)) == ["둘째(고침)", "첫째(고침)"]


@pytest.mark.parametrize("meanwhile", ["edit", "delete"])
def test_edit_or_delete_between_read_and_update_is_not_overwritten(meanwhile):
    """옷 묶음 저장 함수가 행을 읽은 직후 다른 창에서 '정보 수정'·삭제가 끼어들어도, 그 행은 카드 내용으로 덮어쓰지 않고
    충돌로 알린다 (고치기 조건이 UPDATE 자체에 있음)."""
    backend = FakeBackend()
    client = signed_in_client(backend, "race@example.com")
    store = CloudStore(client, client.uid)
    store.create_user("나", "male", 0, 0)
    card = {"category": "상의", "warmth": 2, "rain_ok": 0, "request_id": _req(1)}
    iid = store.create_wardrobe_items(client.uid, [{**card, "name": "카드"}])[0]
    other = signed_in_client(backend, "race@example.com")                 # 같은 사용자의 다른 창
    so = CloudStore(other, other.uid)
    backend.after_select.append({"edit": lambda: so.update_wardrobe_item(iid, other.uid, "다른 창에서 고침", "상의", None,
                                                                         None, 2, 0),
                                 "delete": lambda: so.soft_delete_item(iid, other.uid)}[meanwhile])
    kept = []
    assert store.create_wardrobe_items(client.uid, [{**card, "name": "카드 재시도"}], conflicts=kept) == [iid]
    row = next(r for r in backend.rows("wardrobe_items") if r["id"] == iid)
    assert kept == [_req(1)] and row["name"] == ("다른 창에서 고침" if meanwhile == "edit" else "카드")
