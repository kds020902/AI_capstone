
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd

from core.catalog import starter_for_gender
from core.taxonomy import LEGACY_RENAMES

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "ai_wardrobe.sqlite3"

# 옛 DB에 남아 있을 수 있는 스타일 열. 앱 시작 시 지운다 (SQLite 3.35 미만이라 못 지우면 빈 값으로만 채움)
LEGACY_STYLE_COLUMNS = [("users", "preferred_style"), ("wardrobe_items", "style"),
                        ("recommendation_sessions", "preferred_style")]

def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c

def _has_column(c, table, column):
    rows = c.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)

def _insert(c, table, values):
    """INSERT 한 줄 → 새 id. 지우지 못한 옛 스타일 열(NOT NULL)이 남아 있으면 빈 값으로 채운다."""
    values = dict(values)
    for t, col in LEGACY_STYLE_COLUMNS:
        if t == table and _has_column(c, table, col):
            values[col] = ""
    cols, marks = ", ".join(values), ", ".join("?" * len(values))
    return c.execute(f"INSERT INTO {table}({cols}) VALUES({marks})", list(values.values())).lastrowid


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                gender TEXT NOT NULL DEFAULT 'male',
                cold_sensitivity REAL NOT NULL DEFAULT 0.4,
                heat_sensitivity REAL NOT NULL DEFAULT 0.4,
                baseline_decision_seconds REAL NOT NULL DEFAULT 180,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wardrobe_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT,
                color TEXT,
                warmth INTEGER NOT NULL,
                rain_ok INTEGER NOT NULL,
                material TEXT,
                image_path TEXT,
                notes TEXT,
                ai_category TEXT,
                ai_subcategory TEXT,
                ai_confidence REAL,
                seasons TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                deleted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS recommendation_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                purpose TEXT NOT NULL,
                temperature REAL NOT NULL,
                apparent_temperature REAL,
                humidity REAL NOT NULL,
                precipitation REAL NOT NULL,
                rain INTEGER NOT NULL,
                wind_speed REAL NOT NULL,
                weather_source TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                decision_seconds REAL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                score REAL NOT NULL,
                pieces_json TEXT NOT NULL,
                reasons_json TEXT NOT NULL,
                components_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES recommendation_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS saved_outfits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                recommendation_id INTEGER NOT NULL,
                saved_at TEXT NOT NULL,
                UNIQUE(user_id, recommendation_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (recommendation_id) REFERENCES recommendations(id)
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                recommendation_id INTEGER NOT NULL,
                rating TEXT,
                worn INTEGER,
                thermal_feedback INTEGER,
                satisfaction INTEGER,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, recommendation_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (recommendation_id) REFERENCES recommendations(id)
            );

            -- 사기 전에 맞춰보기: 아직 안 산 옷. 옷장(wardrobe_items)과 따로 둬서 추천에 섞이지 않게 한다
            CREATE TABLE IF NOT EXISTS purchase_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT,
                color TEXT,
                warmth INTEGER NOT NULL,
                rain_ok INTEGER NOT NULL,
                image_path TEXT,
                link TEXT,
                ai_subcategory TEXT,
                ai_confidence REAL,
                deleted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """
        )

        # 옛 DB 호환: 없는 열 추가
        if not _has_column(c, "users", "gender"):
            c.execute("ALTER TABLE users ADD COLUMN gender TEXT NOT NULL DEFAULT 'male'")
        if not _has_column(c, "wardrobe_items", "subcategory"):
            c.execute("ALTER TABLE wardrobe_items ADD COLUMN subcategory TEXT")
        if not _has_column(c, "wardrobe_items", "ai_subcategory"):
            c.execute("ALTER TABLE wardrobe_items ADD COLUMN ai_subcategory TEXT")
        if not _has_column(c, "wardrobe_items", "seasons"):  # 직접 고른 계절 ('여름|겨울'), NULL이면 종류로 자동
            c.execute("ALTER TABLE wardrobe_items ADD COLUMN seasons TEXT")

        # 옛 DB 호환: 스타일 열 삭제. SQLite 3.35 미만이라 열을 못 지우면 남겨 두고 _insert가 빈 값으로 채운다
        for table, col in LEGACY_STYLE_COLUMNS:
            if _has_column(c, table, col):
                try:
                    c.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
                except sqlite3.OperationalError:
                    pass
        if _has_column(c, "feedback", "style_rating"):  # '스타일 만족도' → '코디 만족도'
            c.execute("ALTER TABLE feedback RENAME COLUMN style_rating TO satisfaction")

        # 옛 DB 호환: '/'가 들어간 클래스명을 현재 이름으로 (폴더명으로 쓸 수 없음)
        for old_name, new_name in LEGACY_RENAMES.items():
            c.execute("UPDATE wardrobe_items SET subcategory=? WHERE subcategory=?", (new_name, old_name))
            c.execute("UPDATE wardrobe_items SET ai_subcategory=? WHERE ai_subcategory=?", (new_name, old_name))

def create_user(name, gender, cold_sensitivity, heat_sensitivity):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        return _insert(c, "users", {
            "name": name, "gender": gender, "cold_sensitivity": cold_sensitivity,
            "heat_sensitivity": heat_sensitivity, "baseline_decision_seconds": 0, "created_at": now,
        })

def list_users():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM users ORDER BY id").fetchall()]

def get_user(user_id):
    with conn() as c:
        r = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(r) if r else None

def update_user(user_id, gender, cold_sensitivity, heat_sensitivity):
    with conn() as c:
        c.execute(
            "UPDATE users SET gender=?, cold_sensitivity=?, heat_sensitivity=? WHERE id=?",
            (gender, cold_sensitivity, heat_sensitivity, user_id),
        )

def create_wardrobe_item(
    user_id, name, category, subcategory, color, warmth, rain_ok,
    material="", image_path="", notes="", ai_category=None, ai_subcategory=None, ai_confidence=None
):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        return _insert(c, "wardrobe_items", {
            "user_id": user_id, "name": name, "category": category, "subcategory": subcategory, "color": color,
            "warmth": warmth, "rain_ok": int(bool(rain_ok)), "material": material, "image_path": image_path,
            "notes": notes, "ai_category": ai_category, "ai_subcategory": ai_subcategory,
            "ai_confidence": ai_confidence, "created_at": now,
        })

def list_wardrobe_items(user_id, active_only=True):
    q = "SELECT * FROM wardrobe_items WHERE user_id=? AND deleted=0"
    args = [user_id]
    if active_only:
        q += " AND active=1"
    q += " ORDER BY id DESC"
    with conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]

def count_wardrobe_items(user_id):
    with conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM wardrobe_items WHERE user_id=? AND active=1 AND deleted=0",
            (user_id,),
        ).fetchone()[0]

def deactivate_item(item_id, user_id):
    with conn() as c:
        c.execute(
            "UPDATE wardrobe_items SET active=0 WHERE id=? AND user_id=?",
            (item_id, user_id),
        )

def reactivate_item(item_id, user_id):
    with conn() as c:
        c.execute(
            "UPDATE wardrobe_items SET active=1 WHERE id=? AND user_id=? AND deleted=0",
            (item_id, user_id),
        )

def update_item_seasons(item_id, user_id, seasons):
    """seasons: 계절 목록. 비우거나 None이면 '자동'(옷 종류로 정함)으로 되돌린다."""
    value = "|".join(seasons) if seasons else None
    with conn() as c:
        c.execute("UPDATE wardrobe_items SET seasons=? WHERE id=? AND user_id=?", (value, item_id, user_id))

def set_items_active(item_ids, user_id, active):
    """여러 벌을 한 번에 넣어두기(active=False) / 꺼내기(active=True). 지운 옷은 꺼내지 않는다."""
    with conn() as c:
        c.executemany("UPDATE wardrobe_items SET active=? WHERE id=? AND user_id=? AND deleted=0",
                      [(int(bool(active)), i, user_id) for i in item_ids])

def list_inactive_items(user_id):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM wardrobe_items WHERE user_id=? AND active=0 AND deleted=0 ORDER BY id DESC",
            (user_id,),
        ).fetchall()]

def soft_delete_item(item_id, user_id):
    with conn() as c:
        c.execute(
            "UPDATE wardrobe_items SET deleted=1, active=0 WHERE id=? AND user_id=?",
            (item_id, user_id),
        )

def create_candidate(user_id, name, category, subcategory, color, warmth, rain_ok, image_path="", link="",
                     ai_subcategory=None, ai_confidence=None):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        return _insert(c, "purchase_candidates", {
            "user_id": user_id, "name": name, "category": category, "subcategory": subcategory, "color": color,
            "warmth": warmth, "rain_ok": int(bool(rain_ok)), "image_path": image_path, "link": link,
            "ai_subcategory": ai_subcategory, "ai_confidence": ai_confidence, "created_at": now,
        })

def list_candidates(user_id):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM purchase_candidates WHERE user_id=? AND deleted=0 ORDER BY id DESC", (user_id,),
        ).fetchall()]

def delete_candidate(candidate_id, user_id):
    with conn() as c:
        c.execute("UPDATE purchase_candidates SET deleted=1 WHERE id=? AND user_id=?", (candidate_id, user_id))

def buy_candidate(candidate_id, user_id):
    """'샀어요': 후보를 옷장에 넣고 후보 목록에서 뺀다. 새 옷장 옷 id (없는 후보면 None)."""
    with conn() as c:
        row = c.execute("SELECT * FROM purchase_candidates WHERE id=? AND user_id=? AND deleted=0",
                        (candidate_id, user_id)).fetchone()
    if row is None:
        return None
    r = dict(row)
    item_id = create_wardrobe_item(user_id, r["name"], r["category"], r["subcategory"], r["color"], r["warmth"],
                                   r["rain_ok"], image_path=r["image_path"] or "", notes=r["link"] or "",
                                   ai_subcategory=r["ai_subcategory"], ai_confidence=r["ai_confidence"])
    delete_candidate(candidate_id, user_id)
    return item_id

def add_starter_wardrobe(user_id):
    user = get_user(user_id)
    if not user:
        return 0

    starter = starter_for_gender(user["gender"])
    existing = {x["name"] for x in list_wardrobe_items(user_id, active_only=False)}
    added = 0
    for row in starter:
        if row[0] in existing:
            continue
        create_wardrobe_item(
            user_id=user_id,
            name=row[0],
            category=row[1],
            subcategory=row[2],
            color=row[3],
            warmth=row[4],
            rain_ok=bool(row[5]),
            material=row[6],
            notes="프로필 성별 기준 자동 추가된 캐주얼 스타터 옷장",
        )
        added += 1
    return added

def create_recommendation_session(
    user_id, purpose, temperature, apparent_temperature,
    humidity, precipitation, rain, wind_speed, weather_source
):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        return _insert(c, "recommendation_sessions", {
            "user_id": user_id, "purpose": purpose, "temperature": temperature,
            "apparent_temperature": apparent_temperature, "humidity": humidity, "precipitation": precipitation,
            "rain": int(bool(rain)), "wind_speed": wind_speed, "weather_source": weather_source, "started_at": now,
        })

def save_recommendation(session_id, rank, result):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        cur = c.execute(
            """
            INSERT INTO recommendations(
                session_id, rank, score, pieces_json, reasons_json, components_json, created_at
            )
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                session_id,
                rank,
                result["score"],
                json.dumps(result["pieces"], ensure_ascii=False),
                json.dumps(result["reasons"], ensure_ascii=False),
                json.dumps(result["components"], ensure_ascii=False),
                now,
            ),
        )
        return cur.lastrowid

def complete_recommendation_session(session_id):
    """코디를 저장하면 세션 완료 처리."""
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        c.execute(
            "UPDATE recommendation_sessions SET completed_at=COALESCE(completed_at, ?) WHERE id=?",
            (now, session_id),
        )

def save_outfit(user_id, recommendation_id):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        c.execute(
            """
            INSERT OR IGNORE INTO saved_outfits(user_id, recommendation_id, saved_at)
            VALUES(?,?,?)
            """,
            (user_id, recommendation_id, now),
        )

def upsert_feedback(
    user_id, recommendation_id, rating=None, worn=None,
    thermal_feedback=None, satisfaction=None
):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        old = c.execute(
            "SELECT * FROM feedback WHERE user_id=? AND recommendation_id=?",
            (user_id, recommendation_id),
        ).fetchone()

        if old:
            old = dict(old)
            c.execute(
                """
                UPDATE feedback
                SET rating=?, worn=?, thermal_feedback=?, satisfaction=?, updated_at=?
                WHERE user_id=? AND recommendation_id=?
                """,
                (
                    rating if rating is not None else old["rating"],
                    int(worn) if worn is not None else old["worn"],
                    thermal_feedback if thermal_feedback is not None else old["thermal_feedback"],
                    satisfaction if satisfaction is not None else old["satisfaction"],
                    now, user_id, recommendation_id
                ),
            )
        else:
            c.execute(
                """
                INSERT INTO feedback(
                    user_id, recommendation_id, rating, worn,
                    thermal_feedback, satisfaction, updated_at
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    user_id, recommendation_id, rating,
                    int(worn) if worn is not None else None,
                    thermal_feedback, satisfaction, now
                ),
            )

def disliked_item_sets(user_id):
    """'별로'를 누른 코디의 옷 id 묶음들. 다음 추천에서 같은 조합을 제외하는 데 쓴다."""
    with conn() as c:
        rows = c.execute(
            """
            SELECT r.pieces_json
            FROM feedback f
            JOIN recommendations r ON r.id=f.recommendation_id
            JOIN recommendation_sessions rs ON rs.id=r.session_id
            WHERE f.user_id=? AND rs.user_id=? AND f.rating='dislike'
            """,
            (user_id, user_id),
        ).fetchall()
    return {frozenset(int(p["id"]) for p in json.loads(r["pieces_json"])) for r in rows}

def count_recommendation_sessions(user_id):
    with conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM recommendation_sessions WHERE user_id=?",
            (user_id,),
        ).fetchone()[0]

def top3_acceptance_rate(user_id):
    with conn() as c:
        total = c.execute(
            "SELECT COUNT(*) FROM recommendation_sessions WHERE user_id=?",
            (user_id,),
        ).fetchone()[0]
        if not total:
            return None
        accepted = c.execute(
            """
            SELECT COUNT(DISTINCT r.session_id)
            FROM saved_outfits s
            JOIN recommendations r ON r.id=s.recommendation_id
            JOIN recommendation_sessions rs ON rs.id=r.session_id
            WHERE s.user_id=? AND rs.user_id=?
            """,
            (user_id, user_id),
        ).fetchone()[0]
        return accepted / total

def list_saved_outfits(user_id):
    with conn() as c:
        rows = c.execute(
            """
            SELECT
                s.saved_at,
                r.rank,
                r.score,
                r.pieces_json,
                rs.purpose,
                rs.temperature,
                rs.humidity
            FROM saved_outfits s
            JOIN recommendations r ON r.id=s.recommendation_id
            JOIN recommendation_sessions rs ON rs.id=r.session_id
            WHERE s.user_id=?
            ORDER BY s.id DESC
            """,
            (user_id,),
        ).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        pieces = json.loads(d["pieces_json"])
        d["outfit_text"] = " + ".join(p["name"] for p in pieces)
        out.append(d)
    return out

def user_kpi_stats(user_id):
    return {
        "completed_sessions": count_recommendation_sessions(user_id),
        "top3_acceptance_rate": top3_acceptance_rate(user_id),
    }

def export_user_logs(user_id):
    with conn() as c:
        rows = c.execute(
            """
            SELECT
                rs.id AS session_id,
                rs.started_at,
                rs.completed_at,
                rs.purpose,
                rs.temperature,
                rs.apparent_temperature,
                rs.humidity,
                rs.precipitation,
                rs.rain,
                rs.wind_speed,
                rs.weather_source,
                r.id AS recommendation_id,
                r.rank,
                r.score,
                r.pieces_json,
                f.rating,
                f.worn,
                f.thermal_feedback,
                f.satisfaction
            FROM recommendation_sessions rs
            LEFT JOIN recommendations r ON r.session_id=rs.id
            LEFT JOIN feedback f ON f.recommendation_id=r.id AND f.user_id=rs.user_id
            WHERE rs.user_id=?
            ORDER BY rs.id DESC, r.rank
            """,
            (user_id,),
        ).fetchall()

    data = []
    for r in rows:
        d = dict(r)
        if d["pieces_json"]:
            pieces = json.loads(d["pieces_json"])
            d["outfit"] = " + ".join(p["name"] for p in pieces)
        d.pop("pieces_json", None)
        data.append(d)
    return pd.DataFrame(data)
