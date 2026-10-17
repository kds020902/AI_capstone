"""실제 Supabase 프로젝트가 앱과 맞게 설정됐는지 확인한다 (supabase/schema.sql 적용 후 실행).

시험용 계정 두 개로 로그인해서 쓰기·읽기·사진 저장과 "다른 계정 데이터가 안 보이는지"를 확인하고,
시험으로 만든 행과 사진은 끝에 지운다. 실제로 쓰는 계정이 아니라 시험용 계정을 쓰세요
(앱에서 미리 가입. 이미 옷이 있는 계정이면 멈춤).

  set SUPABASE_URL=https://xxxx.supabase.co
  set SUPABASE_KEY=<anon public 또는 publishable 키>
  python scripts/check_supabase.py --email-a test1@example.com --email-b test2@example.com
(비밀번호는 실행 중에 물어본다)
"""
from __future__ import annotations

import argparse
import getpass
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from core import cloud_auth  # noqa: E402
from core.cloud_store import CloudStore  # noqa: E402
from core.config import cloud_settings  # noqa: E402
from core.media import BUCKET, SupabaseMedia  # noqa: E402
from core.recommender import recommend  # noqa: E402

TABLES = ["feedback", "saved_outfits", "recommendations", "recommendation_sessions", "purchase_candidates",
          "wardrobe_items"]


def login(settings, email, label):
    client = cloud_auth.make_client(settings)
    user, msg = cloud_auth.sign_in(client, email, getpass.getpass(f"{label} 계정({email}) 비밀번호: "))
    if not user:
        raise SystemExit(f"{label} 로그인 실패: {msg}")
    return client, str(user.id)


def check(name, ok, detail=""):
    print(("통과  " if ok else "실패  ") + name + (f" — {detail}" if detail else ""))
    return ok


def cleanup(client, uid, paths, drop_profile=False):
    for t in TABLES:
        client.table(t).delete().eq("user_id", uid).execute()
    if drop_profile:                         # 이 스크립트가 만든 프로필만 지운다
        client.table("profiles").delete().eq("id", uid).execute()
    if paths:
        client.storage.from_(BUCKET).remove(paths)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email-a", required=True)
    ap.add_argument("--email-b", required=True)
    args = ap.parse_args()
    settings = cloud_settings()
    if not settings:
        raise SystemExit("SUPABASE_URL, SUPABASE_KEY 환경 변수가 필요해요.")
    a, ua = login(settings, args.email_a, "A")
    b, ub = login(settings, args.email_b, "B")
    sa, sb = CloudStore(a, ua), CloudStore(b, ub)
    for s, u in ((sa, ua), (sb, ub)):
        if s.list_wardrobe_items(u, active_only=False):
            raise SystemExit("시험용 계정에 이미 옷이 있어요. 빈 시험용 계정으로 실행해 주세요.")

    results, paths = [], []
    made_profile = sa.get_user(ua) is None
    try:
        if made_profile:
            sa.create_user("시험A", "male", 0, 0)
        results.append(check("프로필 저장·읽기", sa.get_user(ua)["id"] == ua))
        n = sa.add_starter_wardrobe(ua)
        items = sa.list_wardrobe_items(ua)
        results.append(check("스타터 옷장 30벌 저장", n == 30 and len(items) == 30, f"{len(items)}벌"))
        rec = recommend(items, 12, 11, 60, 0, False, 8, "등교", 0, 0, 3)
        sid = sa.create_recommendation_session(ua, "등교", 12, 11, 60, 0, False, 8, "check")
        rid = sa.save_recommendation(sid, 1, rec["results"][0])
        sa.save_outfit(ua, rid)
        sa.save_outfit(ua, rid)
        sa.upsert_feedback(ua, rid, rating="dislike")
        results.append(check("추천 저장·코디 저장(중복 무시)·피드백",
                             len(sa.list_saved_outfits(ua)) == 1 and len(sa.disliked_item_sets(ua)) == 1))
        results.append(check("CSV 기록 만들기", len(sa.export_user_logs(ua)) == 1))

        buf = io.BytesIO()
        Image.new("RGB", (50, 50), (200, 0, 0)).save(buf, "PNG")
        ref = SupabaseMedia(a, ua).save(ua, "check_", buf.getvalue(), ".png")
        paths.append(ref[3:])
        results.append(check("사진 올리기·내려받기", bool(SupabaseMedia(a, ua).read(ref))))
        results.append(check("사진 서명 주소(가상 착용용)", bool(SupabaseMedia(a, ua).signed_url(ref, 60))))

        # 다른 계정 B에서
        results.append(check("B에게 A의 옷이 안 보임", sb.list_wardrobe_items(ub, active_only=False) == []))
        results.append(check("B에게 A의 행이 직접 조회로도 안 보임",
                             b.table("wardrobe_items").select("id").eq("user_id", ua).execute().data == []))
        b.table("wardrobe_items").update({"name": "바뀜"}).eq("id", items[0]["id"]).execute()
        results.append(check("B가 A의 옷을 못 바꿈", sa.list_wardrobe_items(ua)[0]["name"] == items[0]["name"]))
        try:
            b.table("wardrobe_items").insert({"user_id": ua, "name": "x", "category": "상의", "warmth": 1,
                                              "rain_ok": 0, "created_at": "now"}).execute()
            results.append(check("B가 A 이름으로 행을 못 넣음", False))
        except Exception:
            results.append(check("B가 A 이름으로 행을 못 넣음", True))
        results.append(check("B가 A의 사진을 못 읽음", SupabaseMedia(b, ub).read(ref) is None
                             and _cannot_download(b, paths[0])))
        results.append(check("B가 A의 행을 못 지움", _delete_count(b, "wardrobe_items", items[0]["id"]) == 0
                             and len(sa.list_wardrobe_items(ua)) == 30))
    finally:
        cleanup(a, ua, paths, drop_profile=made_profile)
        cleanup(b, ub, [])
    print(f"\n{sum(results)}/{len(results)} 통과 (시험 데이터는 지웠어요)")
    raise SystemExit(0 if all(results) else 1)


def _delete_count(client, table, row_id):
    return len(client.table(table).delete().eq("id", row_id).execute().data or [])


def _cannot_download(client, path):
    try:
        client.storage.from_(BUCKET).download(path)
        return False
    except Exception:
        return True


if __name__ == "__main__":
    main()
