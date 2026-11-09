"""사기 전에 맞춰보기 (구매 후보를 옷장과 맞춰 보기)."""
from core import db
from core.recommender import recommend
from core.shopping import CANDIDATE_ID, candidate_report
from tests.helpers import starter_items


def _cand(name, cat, sub, color, warmth=2, rain_ok=0):
    return {"name": name, "category": cat, "subcategory": sub, "color": color, "warmth": warmth, "rain_ok": rain_ok}


def test_must_include_keeps_only_outfits_with_that_item():
    items = starter_items("male")
    coat = next(x for x in items if x["subcategory"] == "코트")
    out = recommend(items, 5, 5, 60, 0, False, 8, "등교", 0, 0, 3, must_include=coat["id"])
    assert out["results"] and all(coat in r["pieces"] for r in out["results"])
    assert out["n_combos"] == len(out["combo_keys"]) > 0
    assert all(coat["id"] in k for k in out["combo_keys"])
    assert "combo_keys" not in recommend(items, 5, 5, 60, 0, False, 8, "등교", 0, 0, 3)


def test_trench_coat_fits_starter_wardrobe():
    r = candidate_report(_cand("베이지 트렌치코트", "아우터", "코트", "beige", 3), starter_items("male"))
    assert r["verdict"][0] == "fit" and len(r["partners"]) > 10
    assert r["seasons"] == ["봄·가을", "겨울"]
    assert all(r["cells"][("여름", p)]["best"] is None for p in ("등교", "데이트", "운동", "격식"))  # 여름엔 못 입음
    assert r["cells"][("겨울", "운동")]["n"] == 0                                                   # 코트는 운동 금지
    assert r["cells"][("봄·가을", "등교")]["results"]
    assert CANDIDATE_ID not in r["partners"]


def test_same_type_and_color_is_flagged_as_duplicate():
    r = candidate_report(_cand("흰 반팔 티셔츠(새것)", "상의", "반팔 티셔츠", "흰색", 1, 1), starter_items("male"))
    assert r["verdict"][0] == "dup" and "흰 반팔 티셔츠" in r["verdict"][1]
    assert {x["name"] for x in r["similar"]["same_color"]} == {"흰 반팔 티셔츠"}


def test_fills_a_gap_in_a_summer_only_wardrobe():
    keep = ("반팔 티셔츠", "반바지", "청바지", "슬랙스", "셔츠", "스니커즈", "로퍼", "블레이저", "가디건")
    summer = [x for x in starter_items("male") if x["subcategory"] in keep]
    r = candidate_report(_cand("그레이 니트", "상의", "니트", "gray", 3), summer)
    assert r["verdict"][0] == "gap"
    gains = {(x["season"], x["purpose"]) for x in r["improves"]}
    assert ("겨울", "등교") in gains
    assert all(x["after"] > (x["before"] or 0) for x in r["improves"])


def test_nothing_to_wear_with():
    no_shoes = [x for x in starter_items("female") if x["category"] != "신발"]
    r = candidate_report(_cand("레드 니트", "상의", "니트", "red", 3), no_shoes)
    assert r["verdict"][0] == "none" and r["total"] == 0


def test_candidate_is_kept_out_of_closet_until_bought(tmp_path):
    db.DB_PATH = tmp_path / "shop.sqlite3"
    db.init_db()
    uid = db.create_user("쇼핑", "male", 0, 0)
    db.add_starter_wardrobe(uid)
    n_before = len(db.list_wardrobe_items(uid))
    cid = db.create_candidate(uid, "네이비 플리스", "아우터", "플리스", "navy", 3, 0, link="https://example.com/p/1")
    assert [c["name"] for c in db.list_candidates(uid)] == ["네이비 플리스"]
    assert len(db.list_wardrobe_items(uid)) == n_before                     # 추천·옷장에 섞이지 않음
    item_id = db.buy_candidate(cid, uid)
    bought = next(x for x in db.list_wardrobe_items(uid) if x["id"] == item_id)
    assert bought["subcategory"] == "플리스" and bought["notes"] == "https://example.com/p/1"
    assert db.list_candidates(uid) == [] and db.buy_candidate(cid, uid) is None


def test_candidate_combos_equal_filtering_all_combos():
    """구매 후보가 들어갈 자리만 좁혀 만든 조합 = 모든 조합을 만든 뒤 후보가 든 것만 거른 조합 (같은 순서).
    무작위 옷장 300개 × 후보 종류(상의는 겹쳐 입기의 안·위 둘 다, 원피스, 겉옷 필수/선택) × 기온."""
    import random

    from core.recommender import CATEGORIES, _combos
    from core.taxonomy import LAYER_PAIRS, RULES
    rng = random.Random(11)
    layer_subs = sorted({s for pair in LAYER_PAIRS for s in pair if s in RULES})
    by_cat = {c: [s for s, r in RULES.items() if r["main_category"] == c] for c in CATEGORIES}
    by_cat["상의"] = sorted(set(by_cat["상의"]) | set(layer_subs))
    checked = 0
    for _ in range(300):
        items, next_id = [], 1
        for cat, (lo, hi) in {"상의": (0, 4), "하의": (0, 3), "원피스": (0, 2), "아우터": (0, 3), "신발": (1, 2)}.items():
            for _ in range(rng.randint(lo, hi)):
                items.append({"id": next_id, "category": cat, "subcategory": rng.choice(by_cat[cat])})
                next_id += 1
        cand = rng.choice(items + [{"id": 0, "category": "상의", "subcategory": "셔츠"}])   # 가끔은 후보가 풀에 없음
        cand_id, cand["id"] = -1, -1
        pools = {c: [x for x in items if x["category"] == c] for c in CATEGORIES}
        for temp in (-5, 8, 15, 24):
            for outer_required in (True, False):
                full = [c for c in _combos(pools, outer_required, temp) if any(p["id"] == cand_id for p in c)]
                assert _combos(pools, outer_required, temp, only=cand_id) == full
                checked += bool(full)
    assert checked > 500
