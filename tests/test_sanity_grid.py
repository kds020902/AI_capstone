"""상식 회귀 테스트: 조건 조합 전체에서 TOP3에 '말이 안 되는 코디'가 하나도 없어야 한다.

조건: 목적 4 × 기온 9 × 비 2 × 체감성향 5 = 성별당 360가지 (총 720가지)
검사 기준은 추천 엔진의 규칙표(data/clothing_taxonomy.csv)와 '따로' 적어 둔 상식 목록이다.
규칙표를 잘못 고치면 이 테스트가 잡아낸다.
"""
import itertools

from core.recommender import recommend
from core.taxonomy import LAYER_PAIRS
from tests.helpers import THERMAL, starter_items

PURPOSES = ["등교", "데이트", "운동", "격식"]
TEMPS = [-5, 0, 5, 10, 15, 20, 25, 30, 33]

SPORT_BAD = {"스니커즈", "니트", "블라우스", "셔츠", "슬랙스", "블레이저", "로퍼", "원피스", "셔츠원피스",
             "니트원피스", "스커트", "청바지", "데님재킷", "부츠", "치노팬츠", "코트", "캔버스화",
             "터틀넥", "구두", "힐", "플랫슈즈", "샌들", "슬리퍼"}
FORMAL_BAD = {"긴팔 티셔츠", "반바지", "민소매", "후드티", "조거팬츠", "카고팬츠", "러닝화", "바람막이",
              "반팔 티셔츠", "맨투맨", "기능성 티셔츠", "레깅스", "후드집업", "플리스", "샌들", "슬리퍼"}
HOT_THICK = {"니트", "맨투맨", "후드티", "니트원피스", "터틀넥"}
COLD_THIN = {"반팔 티셔츠", "반바지", "민소매"}
OPEN_SHOES = {"샌들", "슬리퍼"}
BLAZER_BAD = {"후드티", "조거팬츠", "러닝화", "반바지", "민소매", "기능성 티셔츠", "레깅스", "슬리퍼"}
DRESS_SHOE_BAD = {"조거팬츠", "반바지", "레깅스", "기능성 티셔츠"}


def violations(pieces, purpose, t, rain, warnings, reasons):
    subs = {p["subcategory"] for p in pieces}
    cats = {p["category"] for p in pieces}
    v = []
    if purpose == "운동" and subs & SPORT_BAD:
        v.append(("운동인데 부적합한 옷", sorted(subs & SPORT_BAD)))
    if purpose == "격식" and subs & FORMAL_BAD:
        v.append(("격식인데 부적합한 옷", sorted(subs & FORMAL_BAD)))
    if t >= 27 and "아우터" in cats:
        v.append(("27℃ 이상인데 겉옷", [p["name"] for p in pieces if p["category"] == "아우터"]))
    if t >= 27 and subs & HOT_THICK:
        v.append(("27℃ 이상인데 두꺼운 상의", sorted(subs & HOT_THICK)))
    if t >= 30 and "긴팔 티셔츠" in subs:
        v.append(("30℃ 이상인데 긴팔", ["긴팔 티셔츠"]))
    if t <= 10 and subs & COLD_THIN:
        v.append(("10℃ 이하인데 반팔/반바지/민소매", sorted(subs & COLD_THIN)))
    if t <= 15 and subs & OPEN_SHOES:
        v.append(("15℃ 이하인데 샌들/슬리퍼", sorted(subs & OPEN_SHOES)))
    need_outer = (t <= 8) if purpose != "운동" else (t <= 0)
    if need_outer and "아우터" not in cats:
        v.append(("추운데 겉옷 없음", []))
    if "블레이저" in subs and subs & BLAZER_BAD:
        v.append(("블레이저와 안 맞는 조합", sorted(subs & BLAZER_BAD)))
    if subs & {"구두", "힐"} and subs & DRESS_SHOE_BAD:
        v.append(("구두·힐과 안 맞는 조합", sorted(subs & DRESS_SHOE_BAD)))
    tops = [p for p in pieces if p["category"] == "상의"]
    if len(tops) > 2 or (len(tops) == 2 and (tops[0]["subcategory"], tops[1]["subcategory"]) not in LAYER_PAIRS):
        v.append(("허용 안 된 상의 겹쳐 입기", [p["subcategory"] for p in tops]))
    if t >= 27 and len(tops) == 2:
        v.append(("27℃ 이상인데 상의 겹쳐 입기", [p["subcategory"] for p in tops]))
    shoes = [p for p in pieces if p["category"] == "신발"]
    if rain and any(not s["rain_ok"] for s in shoes) and not any("비에 약한" in w for w in warnings):
        v.append(("비 오는데 비에 약한 신발 (경고 없음)", [s["name"] for s in shoes]))
    weak = [p for p in pieces if p["category"] in ("신발", "아우터") and not p["rain_ok"]]
    if rain and weak and not any("비에 약한" in r for r in reasons):
        v.append(("비에 약한 옷이 있는데 이유 문구에 없음", [p["name"] for p in weak]))
    return v


def run_grid(gender):
    items = starter_items(gender)
    found, shoe_only, empty = [], [], []
    for purpose, t, rain, thermal in itertools.product(PURPOSES, TEMPS, [False, True], THERMAL):
        cold, heat = THERMAL[thermal]
        out = recommend(items, t, t, 60, 3 if rain else 0, rain, 8, purpose, cold, heat, 3)
        cond = (purpose, t, "비" if rain else "맑음", thermal)
        if not out["results"]:
            empty.append(cond)
        non_shoe = [frozenset(p["id"] for p in r["pieces"] if p["category"] != "신발") for r in out["results"]]
        if len(set(non_shoe)) != len(non_shoe):
            shoe_only.append(cond)
        for rank, r in enumerate(out["results"], 1):
            for kind, detail in violations(r["pieces"], purpose, t, rain, out["warnings"], r["reasons"]):
                found.append((kind, cond, rank, " + ".join(p["name"] for p in r["pieces"]), detail))
    return found, shoe_only, empty


def _report(found):
    lines = [f"{k} | {c} | {rank}위 | {o} | {d}" for k, c, rank, o, d in found[:15]]
    return f"{len(found)}건 위반\n" + "\n".join(lines)


def _check(gender):
    found, shoe_only, empty = run_grid(gender)
    assert not empty, f"추천이 비어 있는 조건: {empty[:5]}"
    assert not shoe_only, f"TOP3에 신발만 다른 코디가 섞인 조건 {len(shoe_only)}건: {shoe_only[:5]}"
    assert not found, _report(found)


def test_grid_male():
    _check("male")


def test_grid_female():
    _check("female")
