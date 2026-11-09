"""옷장 옷·구매 후보 한 줄의 값. 로컬(core/db.py)과 Supabase(core/cloud_store.py)가 같은 기본값·형태로 넣게 한 곳에 둔다.
SQL·권한 처리는 각 저장소에 그대로 둔다."""
from __future__ import annotations


def wardrobe_row(user_id, item, created_at):
    """item: create_wardrobe_item 의 인자 이름을 키로 갖는 dict (빠진 선택 값은 기본값)."""
    req = item.get("request_id")
    return {
        "user_id": user_id, "name": item["name"], "category": item["category"], "subcategory": item.get("subcategory"),
        "color": item.get("color"), "warmth": int(item["warmth"]), "rain_ok": int(bool(item["rain_ok"])),
        "material": item.get("material", ""), "image_path": item.get("image_path", ""), "notes": item.get("notes", ""),
        "ai_category": item.get("ai_category"), "ai_subcategory": item.get("ai_subcategory"),
        "ai_confidence": item.get("ai_confidence"), "created_at": created_at, "request_id": str(req) if req else None,
    }


def candidate_row(user_id, name, category, subcategory, color, warmth, rain_ok, image_path, link, ai_subcategory,
                  ai_confidence, created_at, request_id=None):
    return {
        "user_id": user_id, "name": name, "category": category, "subcategory": subcategory, "color": color,
        "warmth": int(warmth), "rain_ok": int(bool(rain_ok)), "image_path": image_path, "link": link,
        "ai_subcategory": ai_subcategory, "ai_confidence": ai_confidence, "created_at": created_at,
        "request_id": str(request_id) if request_id else None,
    }
