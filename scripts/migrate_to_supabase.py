"""로컬 SQLite 데이터(사용자 한 명)를 공개 배포용 Supabase 계정으로 옮긴다.

옮기는 것: 프로필, 옷장(넣어 둔·지운 옷 포함), 옷 사진, 구매 후보와 사진, 추천 기록·저장 코디·피드백.
추천 기록 안의 옷 id(pieces_json)도 새 id로 바꿔서 '별로' 제외 기능이 그대로 동작하게 한다.

사용 (옮겨 갈 계정으로 먼저 앱에서 가입해 둘 것)
  set SUPABASE_URL=https://xxxx.supabase.co
  set SUPABASE_KEY=<anon public 또는 publishable 키>
  python scripts/migrate_to_supabase.py --list                 # 로컬 사용자 목록
  python scripts/migrate_to_supabase.py --user-id 1 --email me@example.com
  (비밀번호는 실행 중에 물어본다. 화면·파일에 남지 않는다)

이미 데이터가 있는 계정이면 중복을 막기 위해 멈춘다 (--force 로 덧붙이기).
"""
from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.cloud_store import CloudStore  # noqa: E402
from core.config import cloud_settings  # noqa: E402
from core.media import SupabaseMedia  # noqa: E402

DEFAULT_DB = ROOT / "data" / "ai_wardrobe.sqlite3"


def _rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def _photo(media, uid, prefix, path, log):
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        log(f"  사진 없음, 건너뜀: {path}")
        return ""
    return media.save(uid, prefix, p.read_bytes(), p.suffix.lower() or ".jpg")


def migrate(con, local_user_id, client, log=print, force=False):
    """반환: 옮긴 개수 dict. client 는 이미 로그인한 Supabase 클라이언트."""
    session = client.auth.get_session()
    if session is None:
        raise RuntimeError("Supabase 에 로그인하지 못했어요.")
    uid = str(session.user.id)
    store, media = CloudStore(client, uid), SupabaseMedia(client, uid)

    user = _rows(con, "SELECT * FROM users WHERE id=?", (local_user_id,))
    if not user:
        raise SystemExit(f"로컬 사용자 id {local_user_id} 가 없어요. --list 로 확인하세요.")
    user = user[0]
    if store.list_wardrobe_items(uid, active_only=False) and not force:
        raise SystemExit("이 계정에 이미 옷이 있어요. 중복을 막기 위해 멈춥니다 (덧붙이려면 --force).")

    if store.get_user(uid) is None:
        store.create_user(user["name"], user["gender"], user["cold_sensitivity"], user["heat_sensitivity"])
    else:
        store.update_user(uid, user["gender"], user["cold_sensitivity"], user["heat_sensitivity"])

    count = {"items": 0, "photos": 0, "candidates": 0, "sessions": 0, "recommendations": 0, "saved": 0, "feedback": 0}
    item_map, image_map = {}, {}
    for it in sorted(_rows(con, "SELECT * FROM wardrobe_items WHERE user_id=?", (local_user_id,)), key=lambda r: r["id"]):
        ref = _photo(media, uid, "", it.get("image_path"), log)
        count["photos"] += bool(ref)
        new = client.table("wardrobe_items").insert({
            "user_id": uid, "name": it["name"], "category": it["category"], "subcategory": it.get("subcategory"),
            "color": it.get("color"), "warmth": it["warmth"], "rain_ok": it["rain_ok"],
            "material": it.get("material"), "image_path": ref, "notes": it.get("notes"),
            "ai_category": it.get("ai_category"), "ai_subcategory": it.get("ai_subcategory"),
            "ai_confidence": it.get("ai_confidence"), "seasons": it.get("seasons"), "active": it["active"],
            "deleted": it["deleted"], "created_at": it["created_at"],
        }).execute().data[0]
        item_map[it["id"]] = new["id"]
        image_map[it["id"]] = ref
        count["items"] += 1

    for c in sorted(_rows(con, "SELECT * FROM purchase_candidates WHERE user_id=?", (local_user_id,)), key=lambda r: r["id"]):
        ref = _photo(media, uid, "cand_", c.get("image_path"), log)
        count["photos"] += bool(ref)
        client.table("purchase_candidates").insert({
            "user_id": uid, "name": c["name"], "category": c["category"], "subcategory": c.get("subcategory"),
            "color": c.get("color"), "warmth": c["warmth"], "rain_ok": c["rain_ok"], "image_path": ref,
            "link": c.get("link"), "ai_subcategory": c.get("ai_subcategory"), "ai_confidence": c.get("ai_confidence"),
            "deleted": c["deleted"], "created_at": c["created_at"],
        }).execute()
        count["candidates"] += 1

    def remap(pieces_json):
        pieces = json.loads(pieces_json)
        for p in pieces:
            old = p.get("id")
            if isinstance(old, int) and old in item_map:
                p["id"] = item_map[old]
                p["image_path"] = image_map[old]      # 로컬 경로 → 옮긴 사진 참조
            elif p.get("image_path") and not str(p["image_path"]).startswith("sb:"):
                p["image_path"] = ""                   # 로컬 경로는 공개 서버에서 의미가 없다
            p["user_id"] = uid
        return json.dumps(pieces, ensure_ascii=False)

    session_map, rec_map = {}, {}
    for s in sorted(_rows(con, "SELECT * FROM recommendation_sessions WHERE user_id=?", (local_user_id,)), key=lambda r: r["id"]):
        new = client.table("recommendation_sessions").insert({
            "user_id": uid, "purpose": s["purpose"], "temperature": s["temperature"],
            "apparent_temperature": s.get("apparent_temperature"), "humidity": s["humidity"],
            "precipitation": s["precipitation"], "rain": s["rain"], "wind_speed": s["wind_speed"],
            "weather_source": s["weather_source"], "started_at": s["started_at"],
            "completed_at": s.get("completed_at"), "decision_seconds": s.get("decision_seconds"),
        }).execute().data[0]
        session_map[s["id"]] = new["id"]
        count["sessions"] += 1
    if session_map:
        marks = ",".join("?" * len(session_map))
        for r in _rows(con, f"SELECT * FROM recommendations WHERE session_id IN ({marks}) ORDER BY id", list(session_map)):
            new = client.table("recommendations").insert({
                "user_id": uid, "session_id": session_map[r["session_id"]], "rank": r["rank"], "score": r["score"],
                "pieces_json": remap(r["pieces_json"]), "reasons_json": r["reasons_json"],
                "components_json": r["components_json"], "created_at": r["created_at"],
            }).execute().data[0]
            rec_map[r["id"]] = new["id"]
            count["recommendations"] += 1
    for s in _rows(con, "SELECT * FROM saved_outfits WHERE user_id=? ORDER BY id", (local_user_id,)):
        if s["recommendation_id"] in rec_map:
            client.table("saved_outfits").insert({"user_id": uid, "recommendation_id": rec_map[s["recommendation_id"]],
                                                  "saved_at": s["saved_at"]}).execute()
            count["saved"] += 1
    for f in _rows(con, "SELECT * FROM feedback WHERE user_id=? ORDER BY id", (local_user_id,)):
        if f["recommendation_id"] in rec_map:
            client.table("feedback").insert({
                "user_id": uid, "recommendation_id": rec_map[f["recommendation_id"]], "rating": f.get("rating"),
                "worn": f.get("worn"), "thermal_feedback": f.get("thermal_feedback"),
                "satisfaction": f.get("satisfaction"), "updated_at": f["updated_at"],
            }).execute()
            count["feedback"] += 1
    return count


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--list", action="store_true", help="로컬 사용자 목록만 보여 주기")
    ap.add_argument("--user-id", type=int)
    ap.add_argument("--email")
    ap.add_argument("--force", action="store_true", help="이미 옷이 있는 계정에도 덧붙이기")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    if args.list or args.user_id is None:
        for u in _rows(con, "SELECT u.id, u.name, (SELECT COUNT(*) FROM wardrobe_items w WHERE w.user_id=u.id) AS n "
                            "FROM users u ORDER BY u.id"):
            print(f"id {u['id']}: {u['name']} (옷 {u['n']}벌)")
        return
    settings = cloud_settings()
    if not settings or not args.email:
        raise SystemExit("SUPABASE_URL, SUPABASE_KEY 환경 변수와 --email 이 필요해요.")
    from core import cloud_auth
    client = cloud_auth.make_client(settings)
    user, msg = cloud_auth.sign_in(client, args.email, getpass.getpass("Supabase 계정 비밀번호: "))
    if not user:
        raise SystemExit(msg)
    count = migrate(con, args.user_id, client, force=args.force)
    print("옮김:", ", ".join(f"{k} {v}" for k, v in count.items()))


if __name__ == "__main__":
    main()
