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
    """운동인데 운동화가 하나도 없으면 결과를 비우지 않고 이유를 알려준다."""
    items = [p for p in starter_items("female") if p["subcategory"] not in ("러닝화", "스니커즈")]
    out = recommend(items, 15, 15, 60, 0, False, 8, "운동", 0, 0, 3)
    assert out["results"]
    assert any("운동" in w and "신발" in w for w in out["warnings"])


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
                        assert out["context"]["eff"] < LAYER_MAX_TEMP
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
    """운동엔 러닝화, 격식엔 긴팔 티셔츠 제외 — 규칙표(blocked_purposes)로 거른다."""
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
    assert [x["항목"] for x in rows] == ["날씨 적합도", "비/강수 적합도", "색상 조합"]
    assert sum(float(x["가중치"].rstrip("%")) for x in rows) == 100


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
