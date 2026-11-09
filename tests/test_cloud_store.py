"""공개 배포용 저장소(core/cloud_store.py)가 로컬 SQLite 판(core/db.py)과 같은 결과를 내는지,
다른 계정의 데이터가 섞이지 않는지 확인한다 (가짜 Supabase 사용)."""
import pytest

from core import db
from core.cloud_store import CloudStore
from core.recommender import outfit_key, recommend
from tests.fake_supabase import FakeAPIError, FakeBackend, signed_in_client

REQ, REQ2 = "6f1c1e0a-3a52-4c63-9a0e-2f1d1a0b9c01", "6f1c1e0a-3a52-4c63-9a0e-2f1d1a0b9c02"
DROP = {"id", "user_id", "created_at", "started_at", "completed_at", "saved_at", "updated_at", "session_id",
        "recommendation_id"}


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


def test_same_request_id_registers_once(tmp_path):
    """사진 등록·구매 후보: 같은 요청 id로 다시 저장하면(응답 유실 뒤 재시도) 새로 넣지 않고 처음 id를 돌려준다 (두 저장소 같음)."""
    db.DB_PATH = tmp_path / "once.sqlite3"
    db.init_db()
    local_uid = db.create_user("나", "male", 0, 0)
    client = signed_in_client(FakeBackend(), "once@example.com")
    cloud = CloudStore(client, client.uid)
    cloud.create_user("나", "male", 0, 0)
    rows = [{"name": f"셔츠{i}", "category": "상의", "subcategory": "셔츠", "color": "white", "warmth": 2,
             "rain_ok": 0, "image_path": f"p{i}.jpg", "request_id": f"6f1c1e0a-3a52-4c63-9a0e-00000000000{i}"}
            for i in range(3)]
    out = {}
    for name, store, uid in (("local", db, local_uid), ("cloud", cloud, client.uid)):
        first = store.create_wardrobe_items(uid, rows[:2])
        again = store.create_wardrobe_items(uid, rows)                 # 앞 2벌은 이미 있음, 셋째만 새로
        cand = store.create_candidate(uid, "후보", "아우터", "코트", "beige", 3, 0, request_id=REQ)
        out[name] = (len(first), again[:2] == first, len(set(again)),
                     store.create_candidate(uid, "후보(고침)", "아우터", "코트", "beige", 3, 0, request_id=REQ) == cand,
                     sorted(x["name"] for x in store.list_wardrobe_items(uid)),
                     [c["name"] for c in store.list_candidates(uid)])
        store.create_wardrobe_items(uid, [{**rows[0], "request_id": None, "name": "요청 id 없음"}] * 2)  # 예전처럼 매번 새로
        assert len(store.list_wardrobe_items(uid)) == 5
    assert out["local"] == out["cloud"] == (2, True, 3, True, ["셔츠0", "셔츠1", "셔츠2"], ["후보"])


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
