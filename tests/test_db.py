"""DB 관련 수정 사항 테스트 (임시 DB 파일 사용)."""
import sqlite3

from core import db
from core.recommender import outfit_key, recommend_outfits


def _use_tmp_db(tmp_path):
    db.DB_PATH = tmp_path / "test.sqlite3"
    db.init_db()


def test_legacy_subcategory_is_migrated(tmp_path):
    _use_tmp_db(tmp_path)
    uid = db.create_user("a", "male", 0, 0)
    db.create_wardrobe_item(uid, "필드재킷", "아우터", "야상/필드재킷", "khaki", 3, 1,
                            ai_subcategory="야상/필드재킷")
    db.init_db()  # 앱 재시작
    item = db.list_wardrobe_items(uid)[0]
    assert item["subcategory"] == "야상·필드재킷"
    assert item["ai_subcategory"] == "야상·필드재킷"


def test_duplicate_user_name_raises_integrity_error(tmp_path):
    _use_tmp_db(tmp_path)
    db.create_user("같은이름", "male", 0, 0)
    try:
        db.create_user("같은이름", "female", 0, 0)
    except sqlite3.IntegrityError:
        return
    raise AssertionError("중복 이름이 허용됨")


def test_deactivated_item_can_be_restored(tmp_path):
    _use_tmp_db(tmp_path)
    uid = db.create_user("b", "female", 0, 0)
    db.add_starter_wardrobe(uid)
    item = db.list_wardrobe_items(uid)[0]
    db.deactivate_item(item["id"], uid)
    assert item["id"] in {x["id"] for x in db.list_inactive_items(uid)}
    db.reactivate_item(item["id"], uid)
    assert item["id"] in {x["id"] for x in db.list_wardrobe_items(uid, active_only=True)}


def test_dislike_round_trip(tmp_path):
    _use_tmp_db(tmp_path)
    uid = db.create_user("c", "male", 0, 0)
    db.add_starter_wardrobe(uid)
    items = db.list_wardrobe_items(uid)
    sid = db.create_recommendation_session(uid, "등교", 18, 18, 60, 0, False, 8, "manual")
    first = recommend_outfits(items, 18, 18, 60, 0, False, 8, "등교", 0, 0, 3)[0]
    rid = db.save_recommendation(sid, 1, first)
    db.upsert_feedback(uid, rid, rating="dislike")
    assert outfit_key(first["pieces"]) in db.disliked_item_sets(uid)
    # 다른 사용자의 '별로'는 섞이지 않는다
    other = db.create_user("d", "male", 0, 0)
    assert db.disliked_item_sets(other) == set()


def test_session_complete_without_decision_time(tmp_path):
    _use_tmp_db(tmp_path)
    uid = db.create_user("e", "male", 0, 0)
    sid = db.create_recommendation_session(uid, "등교", 18, 18, 60, 0, False, 8, "manual")
    db.complete_recommendation_session(sid)
    logs = db.export_user_logs(uid)
    assert "decision_seconds" not in logs.columns


# ---------------------------------------------------------------- 옛 DB 호환 (스타일 열)
V4_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    gender TEXT NOT NULL DEFAULT 'male', preferred_style TEXT NOT NULL DEFAULT 'casual',
    cold_sensitivity REAL NOT NULL DEFAULT 0.4, heat_sensitivity REAL NOT NULL DEFAULT 0.4,
    baseline_decision_seconds REAL NOT NULL DEFAULT 180, created_at TEXT NOT NULL);
CREATE TABLE wardrobe_items (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL,
    category TEXT NOT NULL, subcategory TEXT, style TEXT NOT NULL, color TEXT, warmth INTEGER NOT NULL,
    rain_ok INTEGER NOT NULL, material TEXT, image_path TEXT, notes TEXT, ai_category TEXT, ai_subcategory TEXT,
    ai_confidence REAL, active INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, FOREIGN KEY (user_id) REFERENCES users(id));
CREATE TABLE recommendation_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    purpose TEXT NOT NULL, preferred_style TEXT NOT NULL, temperature REAL NOT NULL, apparent_temperature REAL,
    humidity REAL NOT NULL, precipitation REAL NOT NULL, rain INTEGER NOT NULL, wind_speed REAL NOT NULL,
    weather_source TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, decision_seconds REAL,
    FOREIGN KEY (user_id) REFERENCES users(id));
CREATE TABLE feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    recommendation_id INTEGER NOT NULL, rating TEXT, worn INTEGER, thermal_feedback INTEGER, style_rating INTEGER,
    updated_at TEXT NOT NULL, UNIQUE(user_id, recommendation_id));
INSERT INTO users(name, preferred_style, created_at) VALUES('옛사용자', 'formal', '2026-01-01T00:00:00');
INSERT INTO wardrobe_items(user_id, name, category, subcategory, style, color, warmth, rain_ok, created_at)
    VALUES(1, '화이트 셔츠', '상의', '셔츠', 'formal', 'white', 2, 1, '2026-01-01T00:00:00');
INSERT INTO feedback(user_id, recommendation_id, style_rating, updated_at) VALUES(1, 7, 4, '2026-01-01T00:00:00');
"""


def _columns(table):
    with db.conn() as c:
        return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def test_old_db_style_columns_are_removed(tmp_path):
    db.DB_PATH = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db.DB_PATH) as c:
        c.executescript(V4_SCHEMA)
    db.init_db()
    assert "preferred_style" not in _columns("users")
    assert "style" not in _columns("wardrobe_items")
    assert "preferred_style" not in _columns("recommendation_sessions")
    assert "satisfaction" in _columns("feedback") and "style_rating" not in _columns("feedback")
    # 기존 데이터는 그대로
    assert db.list_users()[0]["name"] == "옛사용자"
    assert db.list_wardrobe_items(1)[0]["name"] == "화이트 셔츠"
    with db.conn() as c:
        assert c.execute("SELECT satisfaction FROM feedback").fetchone()[0] == 4
    # 새로 넣기도 된다
    uid = db.create_user("새사용자", "female", 0, 0)
    db.add_starter_wardrobe(uid)
    db.create_recommendation_session(uid, "등교", 18, 18, 60, 0, False, 8, "manual")


def test_insert_fills_style_column_that_old_sqlite_could_not_drop(tmp_path):
    """SQLite 3.35 미만은 DROP COLUMN이 안 된다 → 남은 NOT NULL 스타일 열은 빈 값으로 채워 넣는다."""
    db.DB_PATH = tmp_path / "old.sqlite3"
    with sqlite3.connect(db.DB_PATH) as c:
        c.executescript(V4_SCHEMA)
    with db.conn() as c:  # init_db를 거치지 않은 상태 = 열을 못 지운 상태
        uid = db._insert(c, "users", {"name": "x", "gender": "male", "cold_sensitivity": 0, "heat_sensitivity": 0,
                                       "baseline_decision_seconds": 0, "created_at": "now"})
        iid = db._insert(c, "wardrobe_items", {"user_id": uid, "name": "y", "category": "상의", "color": "black",
                                                "warmth": 1, "rain_ok": 1, "created_at": "now"})
        assert c.execute("SELECT style FROM wardrobe_items WHERE id=?", (iid,)).fetchone()[0] == ""
