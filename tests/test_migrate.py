"""로컬 SQLite → Supabase 옮기기 (가짜 Supabase)."""
import io
import sqlite3

import pytest
from PIL import Image

from core import db
from core.cloud_store import CloudStore
from core.media import SupabaseMedia
from core.recommender import recommend
from scripts.migrate_to_supabase import migrate
from tests.fake_supabase import FakeBackend, signed_in_client


def _local_user_with_history(tmp_path):
    db.DB_PATH = tmp_path / "old.sqlite3"
    db.init_db()
    uid = db.create_user("옛사용자", "male", 1.0, 0.0)
    db.add_starter_wardrobe(uid)
    photo = tmp_path / "shirt.png"
    Image.new("RGB", (40, 60), (10, 200, 10)).save(photo)
    shirt = db.create_wardrobe_item(uid, "초록 셔츠", "상의", "셔츠", "green", 2, 0, image_path=str(photo))
    items = db.list_wardrobe_items(uid)
    db.update_item_seasons(items[3]["id"], uid, ["겨울"])
    db.soft_delete_item(items[5]["id"], uid)
    db.create_candidate(uid, "레드 니트", "상의", "니트", "red", 3, 0, image_path=str(photo), link="https://x")
    items = db.list_wardrobe_items(uid)
    rec = recommend(items, 12, 11, 60, 0, False, 8, "등교", 0, 0, 3, seed="m")
    sid = db.create_recommendation_session(uid, "등교", 12, 11, 60, 0, False, 8, "manual")
    rids = [db.save_recommendation(sid, i, r) for i, r in enumerate(rec["results"], 1)]
    db.save_outfit(uid, rids[0])
    db.upsert_feedback(uid, rids[1], rating="dislike")
    db.upsert_feedback(uid, rids[0], worn=True, satisfaction=5)
    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    return con, uid, shirt, rec


def test_migrate_keeps_everything_and_remaps_ids(tmp_path):
    con, local_uid, shirt_id, rec = _local_user_with_history(tmp_path)
    client = signed_in_client(FakeBackend(), "me@example.com")
    count = migrate(con, local_uid, client, log=lambda *_: None)
    assert count == {"items": 31, "photos": 2, "candidates": 1, "sessions": 1, "recommendations": 3,
                     "saved": 1, "feedback": 2}

    s = CloudStore(client, client.uid)
    assert s.get_user(client.uid)["cold_sensitivity"] == 1.0
    local_names = sorted(x["name"] for x in db.list_wardrobe_items(local_uid))
    assert sorted(x["name"] for x in s.list_wardrobe_items(client.uid)) == local_names
    assert sorted(x["seasons"] or "" for x in s.list_wardrobe_items(client.uid)) == \
        sorted(x["seasons"] or "" for x in db.list_wardrobe_items(local_uid))
    shirt = next(x for x in s.list_wardrobe_items(client.uid) if x["name"] == "초록 셔츠")
    img = Image.open(io.BytesIO(SupabaseMedia(client, client.uid).read(shirt["image_path"])))
    assert img.size == (40, 60)
    assert s.list_candidates(client.uid)[0]["image_path"].startswith("sb:")
    # '별로' 조합이 새 id로 그대로 제외된다
    new_by_name = {x["name"]: x["id"] for x in s.list_wardrobe_items(client.uid, active_only=False)}
    expected = frozenset(new_by_name[p["name"]] for p in rec["results"][1]["pieces"])
    assert s.disliked_item_sets(client.uid) == {expected}
    assert [x["outfit_text"] for x in s.list_saved_outfits(client.uid)] == \
        [x["outfit_text"] for x in db.list_saved_outfits(local_uid)]
    assert s.top3_acceptance_rate(client.uid) == db.top3_acceptance_rate(local_uid)


def test_migrate_refuses_to_duplicate(tmp_path):
    con, local_uid, *_ = _local_user_with_history(tmp_path)
    client = signed_in_client(FakeBackend(), "me@example.com")
    migrate(con, local_uid, client, log=lambda *_: None)
    with pytest.raises(SystemExit):
        migrate(con, local_uid, client, log=lambda *_: None)
