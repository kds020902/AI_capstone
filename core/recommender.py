"""코디 추천 엔진.

처리 순서
1) 온도 (core/eligibility.py): 실제 체감 T_env, 추천 기준 T_filter(실외 운동이면 +5℃),
   두께 취향 T_pref(체질 ±3℃, 쌀쌀한 비면 최대 −1℃). 체질은 '입을 수 있는 옷'을 바꾸지 않는다.
2) 후보 자격: 옷마다 목적·기온(T_filter)이 맞는지 본다. 절대 금지(운동 + 구두 등)는 어떤 경우에도 빼고,
   코디가 안 나올 때만 단계적으로 완화한다: 정상 → 목적 완화 → 기온 완화 → 둘 다.
   코디가 하나라도 나오는 첫 단계에서 멈추므로 완화한 옷이 든 코디가 정상 코디와 섞이지 않는다.
   그 단계 안에서도 완화한 옷이 적은 코디를 앞에 둔다 (겉옷만 없을 때 상의·신발까지 바꾸지 않게).
   겉옷이 필요한 날(추천 기준 12℃ 이하) 맞는 겉옷이 없으면 경고하고 겉옷 없이 다시 찾는다.
   평소 피하는 조합·'별로' 누른 조합을 빼고, 그러면 하나도 안 남을 때만 경고 후 다시 넣는다.
3) 점수: 맑은 날 = 날씨(두께) 점수. 비 오는 날 = 0.7 × 날씨 + 0.3 × (1 − 젖음 부담).
   젖음 부담 = 0.5 신발 + 0.3 하체(하의·원피스) + 0.2 가장 바깥 상체 — 실제로 비를 맞는 옷만 본다.
4) 순위: 점수를 0.0001 단위 정수로 바꿔 0.02 칸으로 묶고 (완화한 옷 수 →) 칸 → 색 조합 → 점수 → 고정 무작위 순.
   색은 점수가 거의 같을 때 순서만 정한다. 고정 무작위 = seed(앱은 '사용자-날짜')와 옷 id의 해시라
   같은 날엔 같은 결과, 날마다 동점 코디가 바뀌고, 웹 체험판과도 순서가 같다.
5) TOP-K 다양화: 신발만 다른 코디를 같이 내보내지 않음. 고른 뒤에는 원래 순위대로 둔다.
6) 일교차: 귀가 전까지 가장 추운 때(later)가 지금보다 LATER_DROP 이상 추우면, 코디마다
   그때 입을 겉옷을 옷장에서 골라 '챙기세요'로 안내 (코디 자체는 지금 날씨 기준)

코디 구성: 원피스+신발 2벌 ~ 이너+상의+하의+겉옷+신발 5벌.
상의 겹쳐 입기는 LAYER_PAIRS 조합만, 추천 기준 LAYER_MAX_TEMP 미만에서만.
"""
from __future__ import annotations

import math
from itertools import product

from core.colors import color_score
from core.eligibility import TIERS, allowed, item_flags, temp_ok, temperature_context, wet_burden
from core.taxonomy import INCOMPATIBLE_PAIRS, LAYER_PAIRS, canonical_subcategory, rule_for

RAIN_MIX = {"weather": 0.7, "wet": 0.3}               # 비 오는 날 점수 비율 (실험값)
WET_PARTS = {"신발": 0.50, "하체": 0.30, "상체": 0.20}  # 젖음 부담에서 비를 맞는 부위별 비중 (실험값)
SCORE_SCALE = 10000      # 점수를 0.0001 단위 정수로 바꿔 순위를 매긴다 (파이썬·웹이 같은 칸에 넣도록)
BUCKET = 200             # 0.02(=200/10000) 칸 안의 점수 차이는 사실상 같다고 보고 색으로 순서를 정한다

# 각 옷의 역할. 상의를 겹쳐 입으면 안에 입은 옷은 '이너'
# 보온 기여도: 카테고리별, 사용자가 매긴 두께 1~5 → 보온값
# 원피스 = 상의 + 하의 몫 (상의 몫만 주면 원피스가 늘 '춥게' 계산됨)
# 이너 = 상의 몫의 약 60% (겹쳐 입으면 단독으로 입을 때보다 덜 보탬)
WARMTH_CONTRIB = {
    "상의": [0.50, 0.90, 1.30, 1.60, 1.90],
    "이너": [0.30, 0.55, 0.80, 1.00, 1.15],
    "하의": [0.25, 0.50, 0.70, 0.90, 1.10],
    "원피스": [0.75, 1.40, 2.00, 2.50, 3.00],
    "아우터": [0.40, 0.80, 1.30, 1.90, 2.60],
    "신발": [0.10, 0.15, 0.20, 0.30, 0.40],
}

# 두께 취향 온도(℃) → 목표 보온값. 가장 얇은 조합(반팔+반바지+신발=0.85)이 30℃의 목표와 일치하도록 보정
# (최저 목표가 가장 얇은 조합보다 크면 더운 날 겉옷을 덧입히는 게 오히려 점수가 높아짐)
TARGET_CURVE = [
    (-10, 5.00), (-3, 4.60), (3, 4.10), (7, 3.60), (11, 3.10), (15, 2.50),
    (18, 2.00), (21, 1.55), (24, 1.15), (27, 1.00), (30, 0.85),
]

OUTER_REQUIRED_AT = 12.0  # 추천 기준이 이 이하면 겉옷 필수 (맞는 겉옷이 없으면 경고 후 완화)
LAYER_MAX_TEMP = 24.0    # 추천 기준이 이 이상이면 상의를 겹쳐 입히지 않음
WARMTH_TOLERANCE = 1.5   # 목표와 보온값 차이가 이만큼 나면 날씨 점수 0
FIT_GAP = 0.35           # 목표 보온값과 이만큼 넘게 차이 나면 '서늘/더울 수 있다'고 본다 (이유 문구·저녁 안내 공통)
THICK_OUTER_AT = 5.0     # 추천 기준이 이 이하인데
THICK_LEVEL = 4          # 이 두께 이상의 겉옷이 없으면 보온 부족 경고
LATER_DROP = 3.0         # 귀가 전 가장 추울 때가 지금보다 이만큼 이상 추울 때만 따로 안내
MAX_COMBOS = 60000

CATEGORIES = ("상의", "하의", "원피스", "아우터", "신발")
CAT_LABEL = {"상의": "상의", "하의": "하의", "원피스": "원피스", "아우터": "겉옷", "신발": "신발"}
NO_COMBO = "추천에 필요한 옷 조합(상의+하의+신발 또는 원피스+신발)이 없습니다."


# ---------------------------------------------------------------- 보온
def target_warmth(temp):
    pts = TARGET_CURVE
    if temp <= pts[0][0]:
        return pts[0][1]
    if temp >= pts[-1][0]:
        return pts[-1][1]
    for (t0, w0), (t1, w1) in zip(pts, pts[1:]):
        if t0 <= temp <= t1:
            return w0 + (w1 - w0) * (temp - t0) / (t1 - t0)
    return pts[-1][1]


def _level(item):
    try:
        return min(5, max(1, int(round(float(item["warmth"])))))
    except (TypeError, ValueError, KeyError):
        return 2


def _sub(item):
    return canonical_subcategory(item.get("subcategory"))


def roles(pieces):
    """각 옷의 역할 목록. 상의가 두 벌이면 LAYER_PAIRS에서 안쪽에 해당하는 옷이 '이너'."""
    tops = [p for p in pieces if p["category"] == "상의"]
    inner = None
    if len(tops) == 2:
        a, b = tops
        inner = b if (_sub(b), _sub(a)) in LAYER_PAIRS else a
    return ["이너" if p is inner else p["category"] for p in pieces]


def outfit_warmth(pieces):
    # sum()은 파이썬 3.12부터 보정 덧셈이라 웹(JS)과 마지막 자리가 달라질 수 있어 차례로 더한다
    total = 0.0
    for p, r in zip(pieces, roles(pieces)):
        total += WARMTH_CONTRIB.get(r, [0] * 5)[_level(p) - 1]
    return total


def _josa(word, with_final="이", without_final="가"):
    ch = word[-1]
    if "가" <= ch <= "힣":
        return with_final if (ord(ch) - 0xAC00) % 28 else without_final
    return with_final


# ---------------------------------------------------------------- 후보 자격 단계
def _pools(usable, flags, tier):
    pools = {c: [] for c in CATEGORIES}
    for x in usable:
        if allowed(flags[id(x)], tier):
            pools[x["category"]].append(x)
    return pools


def _feasible(pools, outer_required):
    return bool(pools["신발"] and ((pools["상의"] and pools["하의"]) or pools["원피스"])
                and (pools["아우터"] or not outer_required))


def _choose_tier(usable, flags, outer_required):
    """코디가 나오는 첫 단계와 그 단계의 카테고리별 후보. 어느 단계로도 안 되면 (None, None)."""
    for tier in range(len(TIERS)):
        pools = _pools(usable, flags, tier)
        if _feasible(pools, outer_required):
            return tier, pools
    return None, None


def _relax_warnings(usable, flags, tier, pools, outer_required, ctx):
    """정상 단계에서 비어 있던 카테고리마다 무엇을 완화했는지 알린다."""
    p0 = _pools(usable, flags, 0)
    need = ["신발"] + (["아우터"] if outer_required else [])
    if pools["상의"] and pools["하의"]:
        need += ["상의", "하의"]
    if pools["원피스"]:
        need.append("원피스")
    by_tier = {t: _pools(usable, flags, t) for t in (1, 2)} if tier == 3 else {}
    purpose, t = ctx["purpose"], ctx["t_filter"]
    out = []
    for cat in CATEGORIES:
        if cat not in need or p0[cat] or not pools[cat]:
            continue
        label = CAT_LABEL[cat]
        kind = tier if tier < 3 else (1 if by_tier[1][cat] else 2 if by_tier[2][cat] else 3)
        if kind == 1:
            out.append(f"'{purpose}'에 맞는 {label}{_josa(label)} 옷장에 없어 목적 기준을 완화했어요.")
        elif kind == 2:
            out.append(f"추천 기준 {t:.0f}℃에 맞는 {label}{_josa(label)} 옷장에 없어 기온 기준을 완화했어요.")
        else:
            out.append(f"'{purpose}'·추천 기준 {t:.0f}℃에 맞는 {label}{_josa(label)} 옷장에 없어 "
                       "목적·기온 기준을 완화했어요.")
    return out


def _missing(usable, flags, ctx):
    """어떤 단계로도 코디가 안 나올 때, 무엇이 없는지 알린다."""
    p = _pools(usable, flags, len(TIERS) - 1)
    purpose = ctx["purpose"]

    def blocked(cats):
        names = [x["name"] for x in usable if x["category"] in cats and flags[id(x)] is None][:3]
        return f" ({', '.join(names)}{_josa(names[-1], '은', '는')} '{purpose}'에 추천하지 않아요.)" if names else ""

    out = []
    if not p["신발"]:
        out.append(f"'{purpose}'에 신을 수 있는 신발이 옷장에 없어요.{blocked(('신발',))}")
    if not ((p["상의"] and p["하의"]) or p["원피스"]):
        out.append(f"'{purpose}'에 입을 수 있는 상의·하의나 원피스가 옷장에 없어요.{blocked(('상의', '하의', '원피스'))}")
    return out


def _pairs_ok(pieces):
    subs = [_sub(p) for p in pieces if _sub(p)]
    for i in range(len(subs)):
        for j in range(i + 1, len(subs)):
            if frozenset((subs[i], subs[j])) in INCOMPATIBLE_PAIRS:
                return False
    return True


def outfit_key(pieces):
    return frozenset(int(p["id"]) for p in pieces)


def _top_units(tops, temp):
    """상의 한 벌, 또는 LAYER_PAIRS에 있는 두 벌 겹쳐 입기 [안, 위]."""
    units = [[t] for t in tops]
    if temp < LAYER_MAX_TEMP:
        units += [[a, b] for a in tops for b in tops if (_sub(a), _sub(b)) in LAYER_PAIRS]
    return units


def _prune(pools, temp):
    """조합 수가 MAX_COMBOS를 넘으면 착용 기온 범위의 가운데가 추천 기준에서 먼 옷부터 덜어낸다 (id 순서 편향 없음)."""
    def pre(x):
        r = rule_for(x.get("subcategory"))
        if r is None:
            return 0.0
        mid = (max(r["min_temp"], -15) + min(r["max_temp"], 35)) / 2
        return -abs(temp - mid)

    def size():
        sep = len(_top_units(pools["상의"], temp)) * len(pools["하의"])
        return (sep + len(pools["원피스"])) * max(1, len(pools["신발"])) * (len(pools["아우터"]) + 1)

    while size() > MAX_COMBOS:
        cat = max(CATEGORIES, key=lambda c: len(pools[c]))
        if len(pools[cat]) <= 3:
            break
        pools[cat] = sorted(pools[cat], key=pre, reverse=True)[:-1]
    return pools


# ---------------------------------------------------------------- 점수
def exposed(pieces):
    """비를 맞는 옷 (신발, 하체, 가장 바깥 상체). 원피스만 입으면 하체·상체 모두 원피스."""
    by = {r: p for p, r in zip(pieces, roles(pieces))}
    return by.get("신발"), by.get("하의") or by.get("원피스"), by.get("아우터") or by.get("상의") or by.get("원피스")


def seed_rank(seed, pieces):
    """동점일 때 쓰는 고정 무작위 순서: '{seed}|정렬한 옷 id' 의 FNV-1a 32비트 해시 (웹 체험판과 같은 값)."""
    h = 0x811C9DC5
    text = f"{seed}|" + ",".join(str(i) for i in sorted(int(p["id"]) for p in pieces))
    for b in text.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def _score(pieces, ctx):
    """(결과 dict, 순위 키 재료 (칸, 색 점수, 정수 점수))."""
    target = ctx["target"]
    actual = outfit_warmth(pieces)
    weather = max(0.0, 1.0 - abs(actual - target) / WARMTH_TOLERANCE)
    has_outer = any(p["category"] == "아우터" for p in pieces)
    if ctx["rain"]:
        shoe, lower, upper = exposed(pieces)
        wet = (WET_PARTS["신발"] * wet_burden(shoe) + WET_PARTS["하체"] * wet_burden(lower)
               + WET_PARTS["상체"] * wet_burden(upper))
        total = RAIN_MIX["weather"] * weather + RAIN_MIX["wet"] * (1.0 - wet)
    else:
        wet, total = None, weather
    score_i = math.floor(min(1.0, max(0.0, total)) * SCORE_SCALE + 0.5 + 1e-9)
    c_score, c_reason = color_score(pieces)

    # 이유 문구는 실제 계산 결과에서 만든다
    reasons = []
    rs = roles(pieces)
    if "이너" in rs:
        inner, over = pieces[rs.index("이너")], pieces[rs.index("상의")]
        reasons.append(f"{inner['name']} 위에 {over['name']}{_josa(over['name'], '을', '를')} 겹쳐 입는 레이어드 코디예요.")
    where = f"운동 기준 {ctx['t_filter']:.0f}℃" if ctx["purpose"] == "운동" else f"체감 {ctx['t_env']:.0f}℃"
    d = actual - target
    if abs(d) <= FIT_GAP:
        reasons.append(f"{where}에 두께가 잘 맞습니다.")
    elif d < 0:
        reasons.append(f"{where}에는 조금 서늘할 수 있어요. 안에 한 겹 더 입는 걸 권장해요.")
    else:
        reasons.append(f"{where}에는 조금 더울 수 있어요." + (" 겉옷을 벗어 조절하세요." if has_outer else ""))
    if ctx["rain"]:
        weak = []
        for p in (shoe, lower, upper):
            if wet_burden(p) >= 1.0 and p["name"] not in weak:
                weak.append(p["name"])
        if weak:
            reasons.append(f"비에 약한 {', '.join(weak)}{_josa(weak[-1])} 포함돼 있어요. 젖지 않게 주의하세요.")
        else:
            reasons.append("비를 맞는 신발·옷이 비에 강한 조합입니다." + ("" if has_outer else " 우산을 챙기세요."))
    reasons.append(c_reason)

    components = {
        "weather_score": round(weather, 4),
        "rain_score": None if wet is None else round(1.0 - wet, 4),   # 맑은 날은 없음
        "color_score": round(c_score, 4),
        "target_warmth": round(target, 3),
        "actual_warmth": round(actual, 3),
        "env_temp": round(ctx["t_env"], 1),
        "filter_temp": round(ctx["t_filter"], 1),
        "pref_temp": round(ctx["t_pref"], 1),
    }
    return ({"score": score_i / SCORE_SCALE, "reasons": reasons, "components": components},
            (score_i // BUCKET, c_score, score_i))


def _context(temperature, apparent_temperature, rain, purpose, cold, heat, later=None):
    ctx = temperature_context(temperature, apparent_temperature, rain, purpose, cold, heat)
    ctx["target"] = target_warmth(ctx["t_pref"])
    ctx["later"] = None
    if later:
        lt = temperature_context(later["temperature"], later.get("apparent_temperature"), rain, purpose, cold, heat)
        lt["target"] = target_warmth(lt["t_pref"])
        lt["label"] = later.get("label") or "저녁"
        ctx["later"] = lt
    return ctx


def _later_purpose_ok(outer, lt):
    """저녁에 챙길 겉옷 후보는 그 목적에 정상으로 맞는 옷만 (완화·절대 금지 대상은 권하지 않음)."""
    flags = item_flags(outer, lt)
    return flags is not None and not flags[0]


def _later_advice(pieces, ctx, outers):
    """(챙길 겉옷 or None, 안내 문구) 또는 None. 귀가 전 가장 추울 때 이 코디로 모자라면 옷장에서 겉옷을 고른다."""
    lt = ctx["later"]
    if not lt or lt["t_env"] > ctx["t_env"] - LATER_DROP:
        return None
    has_outer = any(p["category"] == "아우터" for p in pieces)
    short = lt["target"] - outfit_warmth(pieces) > FIT_GAP
    if not (short or (not has_outer and lt["t_filter"] <= OUTER_REQUIRED_AT)):
        return None
    when = f"{lt['label']}쯤 체감 {lt['t_env']:.0f}℃까지 내려가요."

    def fit(base, o):  # 그때 목표 보온값에 가까울수록, 같으면 색이 잘 어울릴수록, 그래도 같으면 id 순
        return (round(abs(outfit_warmth(base + [o]) - lt["target"]), 6), -color_score(base + [o])[0], int(o["id"]))

    if has_outer:  # 입고 있는 겉옷보다 두껍고 그때 더 잘 맞는 겉옷이 있으면 그걸 입고 나가라고 안내
        cur = next(p for p in pieces if p["category"] == "아우터")
        rest = [p for p in pieces if p is not cur]
        gap = round(abs(outfit_warmth(pieces) - lt["target"]), 6)
        better = [o for o in outers if int(o["id"]) != int(cur["id"]) and _level(o) > _level(cur)
                  and _later_purpose_ok(o, lt) and temp_ok(o, lt) and _pairs_ok(rest + [o])
                  and fit(rest, o)[0] < gap]
        if not better:
            return None, f"{when} {cur['name']}만으로는 조금 추울 수 있으니 안에 한 겹 더 챙기세요."
        best = min(better, key=lambda o: fit(rest, o))
        return best, (f"{when} {cur['name']}보다 {best['name']}{_josa(best['name'])} 더 따뜻해요. "
                      f"늦게까지 밖에 있으면 {best['name']}{_josa(best['name'], '을', '를')} 입고 나가세요.")
    ok = [o for o in outers if _later_purpose_ok(o, lt) and _pairs_ok(pieces + [o])]
    cands = [o for o in ok if temp_ok(o, lt)] or ok
    if not cands:
        return None, f"{when} 챙길 만한 겉옷이 옷장에 없어요."
    best = min(cands, key=lambda o: fit(pieces, o))
    return best, f"{when} {best['name']}{_josa(best['name'], '을', '를')} 챙기세요."


def _attach_later(result, ctx, outers):
    """결과에 carry(챙길 겉옷)를 달고, 안내 문구를 기온 이유 바로 뒤에 넣는다."""
    result["carry"] = None
    if not ctx["later"]:
        return
    result["components"]["later_env_temp"] = round(ctx["later"]["t_env"], 1)
    advice = _later_advice(result["pieces"], ctx, outers)
    if advice:
        result["carry"], text = advice
        at = next((i + 1 for i, r in enumerate(result["reasons"]) if r.startswith(("체감 ", "운동 기준 "))),
                  len(result["reasons"]))
        result["reasons"].insert(at, text)


def score_outfit(pieces, temperature, apparent_temperature, humidity, precipitation, rain, wind_speed,
                 purpose, cold_sensitivity, heat_sensitivity):
    """단일 코디 (점수, 이유, 구성 요소)."""
    r, _ = _score(pieces, _context(temperature, apparent_temperature, rain, purpose, cold_sensitivity, heat_sensitivity))
    return r["score"], r["reasons"], r["components"]


# ---------------------------------------------------------------- 다양화
def _core(pieces):
    return frozenset(int(p["id"]) for p in pieces if p["category"] in ("상의", "하의", "원피스"))


def _non_shoe(pieces):
    return frozenset(int(p["id"]) for p in pieces if p["category"] != "신발")


def _pick_diverse(scored, k):
    """순위 순으로 고르되, 비슷한 코디는 뒤로 미룬다.
    1차: 상의·하의(원피스)가 모두 다름 → 2차: 겹치는 옷 1개 이하 → 3차: 상·하의 구성이 다름
    → 4차: 신발만 다른 코디는 끝까지 제외. 고른 뒤에는 원래 순위대로 정렬한다.
    """
    picked = []

    def accept(cond):
        for r in scored:
            if len(picked) >= k:
                return
            if any(r is p for p in picked):
                continue
            if all(cond(r, p) for p in picked):
                picked.append(r)

    accept(lambda a, b: not (_core(a["pieces"]) & _core(b["pieces"])))
    accept(lambda a, b: _core(a["pieces"]) != _core(b["pieces"])
           and len(_non_shoe(a["pieces"]) & _non_shoe(b["pieces"])) <= 1)
    accept(lambda a, b: _core(a["pieces"]) != _core(b["pieces"]))
    accept(lambda a, b: _non_shoe(a["pieces"]) != _non_shoe(b["pieces"]))
    picked.sort(key=lambda r: r["rank"])
    return picked


# ---------------------------------------------------------------- 메인
def recommend(items, temperature, apparent_temperature, humidity, precipitation, rain, wind_speed,
              purpose, cold_sensitivity, heat_sensitivity, top_k=3, exclude_keys=None, seed=0, later=None,
              must_include=None):
    """반환: {"results": [...], "warnings": [...], "context": {...}, "n_combos": 후보 코디 수}

    seed: 동점 코디의 순서를 정하는 값. 앱은 '사용자-날짜'를 넘겨 날마다 다른 동점 코디가 나오게 한다.
    later: 귀가 전까지 가장 추운 때 {"temperature", "apparent_temperature", "label": "21시"}. 주면 결과마다
           carry(챙길 겉옷 or None)를 달고 이유에 안내 문구를 넣는다.
    must_include: 이 id의 옷이 들어간 코디만 (사기 전에 맞춰보기). 이때는 조합·'별로' 규칙을 완화하지 않고,
                  후보 코디 목록을 "combo_keys"(옷 id 목록)로도 돌려준다.
    """
    ctx = _context(temperature, apparent_temperature, rain, purpose, cold_sensitivity, heat_sensitivity, later)
    temp = ctx["t_filter"]
    warnings = []
    usable = [x for x in items if x.get("category") in WARMTH_CONTRIB]
    flags = {id(x): item_flags(x, ctx) for x in usable}

    outer_required = temp <= OUTER_REQUIRED_AT
    tier, pools = _choose_tier(usable, flags, outer_required)
    if tier is None and outer_required:
        warnings.append(f"추천 기준 {temp:.0f}℃라 겉옷이 필요하지만 옷장에 맞는 겉옷이 없어요. "
                        "겉옷을 등록하면 날씨에 맞게 추천할 수 있어요.")
        outer_required = False
        tier, pools = _choose_tier(usable, flags, outer_required)
    if tier is None:
        out = {"results": [], "warnings": warnings + [NO_COMBO] + _missing(usable, flags, ctx),
               "context": ctx, "n_combos": 0}
        if must_include is not None:
            out["combo_keys"] = []
        return out
    if tier:
        warnings += _relax_warnings(usable, flags, tier, pools, outer_required, ctx)
    if temp <= THICK_OUTER_AT and not any(_level(x) >= THICK_LEVEL for x in pools["아우터"]):
        warnings.append(f"추천 기준 {temp:.0f}℃인데 두꺼운 겉옷(코트·패딩)이 옷장에 없어 보온이 부족할 수 있어요.")

    if must_include is None:  # 특정 옷을 꼭 넣을 때는 덜어내지 않는다 (그 옷이 빠질 수 있어서)
        pools = _prune(pools, temp)
    outers = pools["아우터"] if outer_required else [None] + pools["아우터"]

    def combos():
        for top, bottom, outer, shoe in product(_top_units(pools["상의"], temp), pools["하의"], outers, pools["신발"]):
            yield top + [bottom] + ([outer] if outer else []) + [shoe]
        for dress, outer, shoe in product(pools["원피스"], outers, pools["신발"]):
            yield [dress] + ([outer] if outer else []) + [shoe]

    candidates = list(combos())
    exclude_keys = set(exclude_keys or ())
    if must_include is not None:
        kept = [c for c in candidates if any(int(p["id"]) == int(must_include) for p in c)
                and _pairs_ok(c) and outfit_key(c) not in exclude_keys]
    else:
        paired = [c for c in candidates if _pairs_ok(c)]
        if not paired:
            warnings.append("옷장 구성상 평소엔 피하는 조합도 포함했어요.")
            paired = candidates
        kept = [c for c in paired if outfit_key(c) not in exclude_keys]
        if not kept:
            warnings.append("'별로'를 누른 조합을 빼면 추천할 코디가 없어 다시 포함했어요.")
            kept = paired
    n_combos = len(kept)
    combo_keys = sorted(sorted(int(p["id"]) for p in c) for c in kept) if must_include is not None else None

    scored = []
    for pieces in kept:
        result, (bucket, c_score, score_i) = _score(pieces, ctx)
        result["pieces"] = pieces
        relaxed = sum(1 for p in pieces if any(flags[id(p)] or ()))
        scored.append(((relaxed, -bucket, -c_score, -score_i, seed_rank(seed, pieces)), result))
    scored.sort(key=lambda x: x[0])
    ranked = []
    for rank, (_, result) in enumerate(scored):
        result["rank"] = rank
        ranked.append(result)
    results = _pick_diverse(ranked, top_k)
    for r in results:
        r.pop("rank", None)
    outer_items = [x for x in usable if x["category"] == "아우터"]
    for r in results:
        _attach_later(r, ctx, outer_items)
    out = {"results": results, "warnings": warnings, "context": ctx, "n_combos": n_combos}
    if combo_keys is not None:
        out["combo_keys"] = combo_keys
    return out


def recommend_outfits(items, temperature, apparent_temperature, humidity, precipitation, rain, wind_speed,
                      purpose, cold_sensitivity, heat_sensitivity, top_k=3, exclude_keys=None, seed=0, later=None):
    """결과 리스트만 반환."""
    return recommend(items, temperature, apparent_temperature, humidity, precipitation, rain, wind_speed,
                     purpose, cold_sensitivity, heat_sensitivity, top_k, exclude_keys, seed, later)["results"]


def explain_score_breakdown(c, purpose=None):
    """점수 근거 표. 맑은 날은 날씨 100%, 비 오는 날은 날씨 70% + 젖음 30%. 색은 점수가 비슷할 때 순서만 정한다."""
    rainy = c.get("rain_score") is not None
    rows = [{"항목": "날씨 적합도 (두께)", "점수": c["weather_score"],
             "가중치": f"{RAIN_MIX['weather'] * 100:.0f}%" if rainy else "100%"}]
    if rainy:
        rows.append({"항목": "비에 젖어도 괜찮은 정도", "점수": c["rain_score"], "가중치": f"{RAIN_MIX['wet'] * 100:.0f}%"})
    rows.append({"항목": "색상 조합", "점수": c.get("color_score", 0), "가중치": "점수가 비슷할 때 순서만"})
    return rows
