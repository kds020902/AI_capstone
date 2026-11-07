"""추천 엔진 단위 테스트."""
from core.colors import color_score, normalize_color
from core.recommender import (
    LAYER_MAX_TEMP, outfit_key, recommend, recommend_outfits, roles, score_outfit, target_warmth,
)
from core.taxonomy import LAYER_PAIRS, rule_for
from tests.helpers import starter_items

ITEMS = [
    {"id": 1, "name": "흰티", "category": "상의", "subcategory": "반팔 티셔츠", "color": "white", "warmth": 1, "rain_ok": 1},
    {"id": 2, "name": "청바지", "category": "하의", "subcategory": "청바지", "color": "blue", "warmth": 2, "rain_ok": 1},
    {"id": 3, "name": "운동화", "category": "신발", "subcategory": "스니커즈", "color": "white", "warmth": 1, "rain_ok": 1},
    {"id": 4, "name": "바람막이", "category": "아우터", "subcategory": "바람막이", "color": "black", "warmth": 1, "rain_ok": 1},
]


def _names(r):
    return {p["name"] for p in r["pieces"]}


def test_top3_basic():
    r = recommend_outfits(ITEMS, 20, 19, 85, 1, True, 8, "등교", .4, .4, 3)
    assert len(r) >= 1
    assert all(0 <= x["score"] <= 1 for x in r)


def test_legacy_items_without_subcategory_still_work():
    legacy = [dict(x, subcategory=None) for x in ITEMS]
    assert recommend_outfits(legacy, 20, 20, 60, 0, False, 8, "등교", 0, 0, 3)


def test_exercise_never_gets_knit():
    """사용자 보고 사례: 운동인데 니트가 뽑힘."""
    for t in (0, 5, 10, 15, 20):
        for r in recommend_outfits(starter_items("male"), t, t, 60, 0, False, 8, "운동", 0, 0, 3):
            assert "베이지 니트" not in _names(r), (t, _names(r))


def test_no_outer_padding_on_hot_day():
    """30℃에 반팔+반바지보다 겉옷을 얹은 코디가 높은 점수를 받으면 안 된다."""
    base = [p for p in starter_items("male") if p["name"] in ("검정 반팔 티셔츠", "네이비 반바지", "그레이 러닝화")]
    jacket = [p for p in starter_items("male") if p["name"] == "블랙 바람막이"]
    s_base, _, _ = score_outfit(base, 30, 30, 60, 0, False, 8, "등교", 0, 0)
    s_more, _, _ = score_outfit(base + jacket, 30, 30, 60, 0, False, 8, "등교", 0, 0)
    assert s_base > s_more


def test_dress_warmth_counts_top_and_bottom():
    """원피스 보온은 상의+하의 몫 → 여러 기온에서 원피스 코디가 추천돼야 한다."""
    items = starter_items("female")
    seen = set()
    for t in (-5, 0, 5, 10, 15, 20, 25):
        for purpose in ("등교", "데이트"):
            for r in recommend_outfits(items, t, t, 60, 0, False, 8, purpose, 0, 0, 3):
                seen |= {p["name"] for p in r["pieces"] if p["category"] == "원피스"}
    assert "블랙 니트원피스" in seen
    assert len(seen) >= 2


def test_target_curve_matches_thinnest_outfit():
    assert abs(target_warmth(30) - 0.85) < 1e-9
    assert target_warmth(-10) > target_warmth(0) > target_warmth(15) > target_warmth(30)


def test_disliked_combo_is_excluded():
    items = starter_items("male")
    first = recommend_outfits(items, 18, 18, 60, 0, False, 8, "등교", 0, 0, 3)[0]
    again = recommend_outfits(items, 18, 18, 60, 0, False, 8, "등교", 0, 0, 3,
                              exclude_keys={outfit_key(first["pieces"])})
    assert all(outfit_key(r["pieces"]) != outfit_key(first["pieces"]) for r in again)


def test_relaxation_warns_when_wardrobe_lacks_items():
    """운동인데 러닝화가 없으면 완화 가능한 신발(스니커즈)로 추천하고 이유를 알려준다."""
    items = [p for p in starter_items("female") if p["subcategory"] != "러닝화"]
    out = recommend(items, 15, 15, 60, 0, False, 8, "운동", 0, 0, 3)
    assert out["results"]
    assert all(next(p for p in r["pieces"] if p["category"] == "신발")["subcategory"] == "스니커즈"
               for r in out["results"])
    assert any("'운동'에 맞는 신발" in w and "목적 기준을 완화" in w for w in out["warnings"])


def test_absolute_purpose_block_is_never_relaxed():
    """운동에 신을 신발이 샌들·로퍼뿐이면 억지로 추천하지 않고, 무엇이 없는지 알려준다."""
    items = [p for p in starter_items("female") if p["subcategory"] not in ("러닝화", "스니커즈")]
    out = recommend(items, 15, 15, 60, 0, False, 8, "운동", 0, 0, 3)
    assert out["results"] == []
    assert any("'운동'에 신을 수 있는 신발이 옷장에 없어요" in w and "로퍼" in w for w in out["warnings"])


def test_relaxed_pieces_are_kept_to_a_minimum():
    """겉옷만 완화가 필요하면 상의·하의·신발은 정상 후보에서 고른다."""
    items = [p for p in starter_items("male") if p["category"] != "아우터"]
    items.append({"id": 99, "name": "데님재킷", "category": "아우터", "subcategory": "데님재킷", "color": "blue",
                  "warmth": 2, "rain_ok": 0})
    out = recommend(items, 7, 7, 60, 0, False, 8, "운동", 0, 0, 3)   # 추천 기준 12℃ → 겉옷 필수
    top = out["results"][0]
    assert any(p["name"] == "데님재킷" for p in top["pieces"])
    assert {p["subcategory"] for p in top["pieces"]} & {"청바지", "슬랙스", "치노팬츠", "카고팬츠", "니트", "스니커즈"} == set()


def test_missing_thick_outer_warning():
    items = [p for p in starter_items("male") if p["subcategory"] not in ("패딩", "코트")]
    out = recommend(items, -5, -5, 60, 0, False, 8, "등교", 0, 0, 3)
    assert any("두꺼운 겉옷" in w for w in out["warnings"])


def test_color_normalization():
    assert normalize_color("#0b0b0b") == "black"
    assert normalize_color("#f4f4f2") == "white"
    assert normalize_color("#1f2a4a") == "navy"
    assert normalize_color("검정") == "black"
    assert normalize_color("네이비") == "navy"
    assert normalize_color("Grey") == "gray"
    assert normalize_color("") is None


def test_color_score_actually_varies():
    """색상 점수가 후보마다 달라야 한다 (hex로 저장된 무채색도 무채색으로 인식)."""
    neutral = [{"color": "black"}, {"color": "white"}]
    accent = [{"color": "black"}, {"color": "red"}]
    clash = [{"color": "red"}, {"color": "green"}, {"color": "yellow"}]
    hex_neutral = [{"color": "#101010"}, {"color": "#fafafa"}]
    s = [color_score(x)[0] for x in (neutral, accent, clash)]
    assert s[1] > s[0] > s[2]
    assert color_score(hex_neutral)[0] == color_score(neutral)[0]  # 업로드 옷(hex)도 무채색 인식


def test_reasons_follow_actual_result():
    """비 오는 날 비에 약한 옷이 들어 있으면 이유 문구에 반드시 알린다."""
    items = [p for p in starter_items("male") if p["subcategory"] != "스니커즈" and p["subcategory"] != "러닝화"]
    out = recommend(items, 15, 15, 60, 3, True, 8, "등교", 0, 0, 3)
    for r in out["results"]:
        weak = [p for p in r["pieces"] if p["category"] in ("신발", "아우터") and not p["rain_ok"]]
        if weak:
            assert any("비에 약한" in x for x in r["reasons"])


# ---------------------------------------------------------------- 코디 구성 (겹쳐 입기)
def test_piece_count_is_not_fixed():
    """원피스+신발 2벌부터 이너+상의+하의+겉옷+신발 5벌까지 나온다."""
    seen = set()
    for gender in ("male", "female"):
        for t in (-5, 5, 12, 20, 30):
            for purpose in ("등교", "데이트", "격식"):
                for r in recommend_outfits(starter_items(gender), t, t, 60, 0, False, 8, purpose, 0, 0, 3):
                    seen.add(len(r["pieces"]))
    assert seen == {2, 3, 4, 5}


def test_layering_only_uses_allowed_pairs():
    layered = 0
    for gender in ("male", "female"):
        for t in (-5, 0, 5, 10, 15, 20, 25, 30):
            for purpose in ("등교", "데이트", "운동", "격식"):
                out = recommend(starter_items(gender), t, t, 60, 0, False, 8, purpose, 0, 0, 3)
                for r in out["results"]:
                    tops = [p for p in r["pieces"] if p["category"] == "상의"]
                    assert len(tops) <= 2
                    if len(tops) == 2:
                        layered += 1
                        assert out["context"]["t_filter"] < LAYER_MAX_TEMP
                        assert (tops[0]["subcategory"], tops[1]["subcategory"]) in LAYER_PAIRS
                        assert roles(r["pieces"])[0] == "이너"
                        assert any("레이어드" in x for x in r["reasons"])
    assert layered > 0


def test_layering_adds_warmth_but_less_than_a_full_top():
    shirt, knit = (next(p for p in starter_items("male") if p["subcategory"] == s) for s in ("셔츠", "니트"))
    bottom = next(p for p in starter_items("male") if p["subcategory"] == "슬랙스")
    _, _, alone = score_outfit([knit, bottom], 10, 10, 60, 0, False, 8, "등교", 0, 0)
    _, _, both = score_outfit([shirt, knit, bottom], 10, 10, 60, 0, False, 8, "등교", 0, 0)
    gain = both["actual_warmth"] - alone["actual_warmth"]
    assert 0 < gain < 0.9  # 셔츠 단독 보온값(0.9)보다 작게


# ---------------------------------------------------------------- 목적 규칙 (운동·격식)
def test_no_style_needed_and_purpose_rules_still_hold():
    """운동엔 러닝화, 격식엔 긴팔 티셔츠 제외 — 규칙표(목적 금지·완화)로 거른다."""
    for gender in ("male", "female"):
        items = starter_items(gender)
        assert all("style" not in x for x in items)
        for t in (-5, 5, 15, 25):
            for r in recommend_outfits(items, t, t, 60, 0, False, 8, "운동", 0, 0, 3):
                assert [p["subcategory"] for p in r["pieces"] if p["category"] == "신발"] == ["러닝화"]
            for r in recommend_outfits(items, t, t, 60, 0, False, 8, "격식", 0, 0, 3):
                assert "긴팔 티셔츠" not in {p["subcategory"] for p in r["pieces"]}


def test_score_breakdown_has_no_style_rows():
    from core.recommender import explain_score_breakdown
    r = recommend_outfits(starter_items("male"), 15, 15, 60, 0, False, 8, "데이트", 0, 0, 1)[0]
    rows = explain_score_breakdown(r["components"])
    assert [x["항목"] for x in rows] == ["날씨 적합도 (두께)", "색상 조합"]          # 맑은 날: 날씨만 점수
    assert rows[0]["가중치"] == "100%" and r["score"] == r["components"]["weather_score"]
    wet = recommend_outfits(starter_items("male"), 15, 15, 60, 3, True, 8, "데이트", 0, 0, 1)[0]
    rows = explain_score_breakdown(wet["components"])
    assert [x["가중치"] for x in rows] == ["70%", "30%", "점수가 비슷할 때 순서만"]


# ---------------------------------------------------------------- 일교차 (귀가 전 가장 추운 때)
LATER_9 = {"temperature": 9, "apparent_temperature": 9, "label": "21시"}


def test_later_cold_suggests_outer_to_carry():
    """낮 21℃ → 21시 9℃: 겉옷 없는 코디엔 그때 맞는 겉옷을 옷장에서 골라 챙기라고 한다."""
    for gender in ("male", "female"):
        for purpose in ("등교", "데이트", "격식"):
            for r in recommend_outfits(starter_items(gender), 21, 21, 60, 0, False, 8, purpose, 0, 0, 3, later=LATER_9):
                advice = [t for t in r["reasons"] if t.startswith("21시쯤 체감 9℃")]
                assert len(advice) == 1, r["reasons"]
                carry = r["carry"]
                if not any(p["category"] == "아우터" for p in r["pieces"]):
                    assert carry and carry["category"] == "아우터" and advice[0].endswith("챙기세요."), advice
                if carry:
                    rule = rule_for(carry["subcategory"])
                    assert purpose not in rule["blocked_purposes"], (purpose, carry["name"])
                    assert rule["min_temp"] <= 9 <= rule["max_temp"], carry["name"]


def test_later_advice_only_when_much_colder():
    for later_t in (19, 22):  # 2℃ 낮거나 오히려 따뜻하면 안내 없음
        later = {"temperature": later_t, "apparent_temperature": later_t, "label": "21시"}
        for r in recommend_outfits(starter_items("male"), 21, 21, 60, 0, False, 8, "등교", 0, 0, 3, later=later):
            assert r["carry"] is None and not any("쯤 체감" in t for t in r["reasons"])
    for r in recommend_outfits(starter_items("male"), 21, 21, 60, 0, False, 8, "등교", 0, 0, 3):
        assert r["carry"] is None and "later_effective_temp" not in r["components"]


def test_later_suggests_warmer_outer_when_wearing_light_one():
    items = ITEMS + [{"id": 5, "name": "울 코트", "category": "아우터", "subcategory": "코트", "color": "gray",
                      "warmth": 4, "rain_ok": 0}]
    windbreaker_outfit = [x for x in items if x["id"] in (1, 2, 3, 4)]
    out = recommend(items, 20, 20, 60, 0, False, 8, "등교", 0, 0, 5,
                    later={"temperature": 6, "apparent_temperature": 6, "label": "저녁"})
    r = next(r for r in out["results"] if {p["id"] for p in r["pieces"]} == {p["id"] for p in windbreaker_outfit})
    assert r["carry"]["name"] == "울 코트"
    assert any("바람막이보다 울 코트가 더 따뜻해요" in t for t in r["reasons"])


# ---------------------------------------------------------------- v2 핵심 장면
def _first(items, t, rain, purpose, cold=0.0, heat=0.0, k=1):
    return recommend(items, t, t, 60, 3 if rain else 0, rain, 8, purpose, cold, heat, k)["results"]


def _subs(r):
    return {p["subcategory"] for p in r["pieces"]}


def test_thermal_type_never_crosses_wearable_range():
    """체질은 '입을 수 있는 옷'을 바꾸지 않는다: 추위를 잘 타도 실외 20℃ 운동(추천 기준 25℃)엔 긴팔 없음,
    더위를 잘 타도 5℃엔 반팔 없음."""
    for gender in ("male", "female"):
        for r in _first(starter_items(gender), 20, False, "운동", cold=1.0, k=3):
            assert "긴팔 티셔츠" not in _subs(r)
        for r in _first(starter_items(gender), 5, False, "등교", heat=1.0, k=3):
            assert not _subs(r) & {"반팔 티셔츠", "반바지", "민소매"}


def test_thermal_type_still_shifts_thickness_within_range():
    items = starter_items("male")
    cold = _first(items, 15, False, "등교", cold=1.0)[0]["components"]["actual_warmth"]
    hot = _first(items, 15, False, "등교", heat=1.0)[0]["components"]["actual_warmth"]
    assert cold > hot


def test_warm_rain_prefers_short_bottoms():
    """따뜻한 비(28℃)엔 젖으면 불편한 긴 바지보다 반바지·치마 쪽이 1위."""
    for gender in ("male", "female"):
        top = _first(starter_items(gender), 28, True, "등교")[0]
        assert _subs(top) & {"반바지", "스커트", "원피스", "셔츠원피스"}, _subs(top)


def test_rainy_exercise_puts_windbreaker_in_top3():
    """비 오는 실외 21℃ 운동: 추천 기준 26℃라 바람막이 범위(24℃)를 넘지만 비를 막는 겉옷이라 후보, TOP 3 안."""
    for gender in ("male", "female"):
        res = _first(starter_items(gender), 21, True, "운동", k=3)
        assert any("바람막이" in _subs(r) for r in res)


def test_rain_score_uses_only_exposed_pieces():
    """겉옷을 입으면 안에 입은 상의는 비를 맞지 않는다: 니트 위 바람막이는 니트 단독보다 젖음 점수가 좋다."""
    knit = {"id": 1, "name": "니트", "category": "상의", "subcategory": "니트", "color": "gray", "warmth": 3, "rain_ok": 0}
    pants = {"id": 2, "name": "반바지", "category": "하의", "subcategory": "반바지", "color": "black", "warmth": 1, "rain_ok": 1}
    boots = {"id": 3, "name": "부츠", "category": "신발", "subcategory": "부츠", "color": "black", "warmth": 3, "rain_ok": 1}
    shell = {"id": 4, "name": "바람막이", "category": "아우터", "subcategory": "바람막이", "color": "black", "warmth": 1,
             "rain_ok": 1}
    _, _, alone = score_outfit([knit, pants, boots], 15, 15, 60, 3, True, 8, "등교", 0, 0)
    _, _, covered = score_outfit([knit, pants, boots, shell], 15, 15, 60, 3, True, 8, "등교", 0, 0)
    assert alone["rain_score"] == 0.8 and covered["rain_score"] == 1.0


def test_user_rain_check_moves_wet_burden_to_nearest_step():
    """사용자가 '비 오는 날 부담이 적음'을 기본값과 다르게 바꾸면 그 판단이 이긴다."""
    from core.eligibility import wet_burden
    jeans = {"subcategory": "청바지", "rain_ok": 1}            # 기본값 그대로 → 규칙표 값 1
    assert wet_burden(jeans) == 1.0
    assert wet_burden({"subcategory": "캔버스화", "rain_ok": 1}) == 0.5   # 사용자가 '괜찮음'으로 바꿈
    assert wet_burden({"subcategory": "반팔 티셔츠", "rain_ok": 0}) == 1.0  # 사용자가 '불편함'으로 바꿈
    assert wet_burden({"subcategory": "없는 종류", "rain_ok": 0}) == 1.0


def test_color_only_breaks_near_ties():
    """색이 아무리 좋아도 0.02 넘게 점수가 낮은 코디가 앞서지 않는다. 같은 칸에서는 색이 좋은 쪽이 앞."""
    for gender in ("male", "female"):
        for t in (-5, 5, 15, 25, 33):
            for rain in (False, True):
                res = _first(starter_items(gender), t, rain, "등교", k=3)
                for a, b in zip(res, res[1:]):
                    assert b["score"] < a["score"] + 0.02 + 1e-9


def test_same_seed_same_order_and_seed_changes_ties():
    items = starter_items("female")
    a = [outfit_key(r["pieces"]) for r in recommend(items, 18, 18, 60, 0, False, 8, "등교", 0, 0, 3, seed="u-1")["results"]]
    b = [outfit_key(r["pieces"]) for r in recommend(items, 18, 18, 60, 0, False, 8, "등교", 0, 0, 3, seed="u-1")["results"]]
    assert a == b
    days = [tuple(outfit_key(r["pieces"]) for r in
                  recommend(items, 18, 18, 60, 0, False, 8, "등교", 0, 0, 3, seed=f"u-{d}")["results"]) for d in range(10)]
    assert len(set(days)) > 1   # 날마다 동점 코디가 바뀐다


def test_random_small_wardrobes_never_break_absolute_rules():
    """작은 옷장 수백 개: 절대 금지(운동 + 구두 등)는 어떤 경우에도 나오지 않고, 결과가 없으면 이유를 알린다."""
    import random
    from core.taxonomy import RULES
    rng = random.Random(7)
    by_cat = {}
    for sub, r in RULES.items():
        by_cat.setdefault(r["main_category"], []).append(sub)
    for n in range(300):
        items, i = [], 0
        for cat, lo, hi in (("상의", 1, 3), ("하의", 1, 3), ("아우터", 0, 2), ("신발", 1, 2), ("원피스", 0, 1)):
            for sub in rng.sample(by_cat[cat], rng.randint(lo, hi)):
                i += 1
                items.append({"id": i, "name": f"{sub}{i}", "category": cat, "subcategory": sub, "color": "black",
                              "warmth": RULES[sub]["default_warmth"], "rain_ok": int(RULES[sub]["default_rain_ok"])})
        purpose = rng.choice(["등교", "데이트", "운동", "격식"])
        t, rain = rng.uniform(-5, 33), rng.random() < 0.3
        out = recommend(items, t, t, 60, 0, rain, 8, purpose, 0, 0, 3)
        for r in out["results"]:
            assert not any(purpose in RULES[p["subcategory"]]["blocked_purposes"] for p in r["pieces"]), (n, purpose)
        if not out["results"]:
            assert len(out["warnings"]) >= 2, out["warnings"]
