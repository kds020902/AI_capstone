"""공개 배포용 저장소(core/cloud_store.py)가 로컬 SQLite 판(core/db.py)과 같은 결과를 내는지,
다른 계정의 데이터가 섞이지 않는지 확인한다 (가짜 Supabase 사용)."""
import pytest

from core import db
from core.cloud_store import CloudStore
from core.recommender import outfit_key, recommend
from tests.fake_supabase import FakeAPIError, FakeBackend, signed_in_client

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


def test_cloud_store_matches_sqlite(tmp_path):
    db.DB_PATH = tmp_path / "parity.sqlite3"
    db.init_db()
    local_uid = db.create_user("동일", "female", 0.6, 0.1)
    local = _scenario(db, local_uid)

    client = signed_in_client(FakeBackend(), "a@example.com")
    cloud = CloudStore(client, client.uid)
    cloud.create_user("동일", "female", 0.6, 0.1)
    remote = _scenario(cloud, client.uid)

    assert local["starter_added"] == 30 and local["starter_again"] == 0
    assert local["disliked_ok"] and remote["disliked_ok"]
    for key in local:
        assert remote[key] == local[key], key


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
    assert sb.buy_candidate(a_cand, b.uid) is None
    assert sa.list_wardrobe_items(a.uid)[0]["seasons"] is None
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
