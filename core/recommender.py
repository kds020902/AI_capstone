"""코디 추천 엔진.

처리 순서
1) 유효 기온 계산: 체감온도 + 체질 보정(±3℃) + 운동 보정(+5℃) + 비(−1℃)
2) 하드 필터: 세부분류 규칙표(data/clothing_taxonomy.csv)로 목적·기온에 안 맞는 옷 제거,
   비 오는 날 비에 약한 신발 제거, 같이 안 입는 조합 제거, '별로' 누른 조합 제거
   → 옷장에 맞는 옷이 없으면 해당 카테고리만 단계적으로 완화하고 경고를 남김
3) 점수: 날씨 60% · 비 25% · 색상 15%
   (목적에 맞는지는 2)의 하드 필터가 맡는다 — 운동엔 러닝화, 격식엔 셔츠·슬랙스·로퍼 등)
4) TOP-K 다양화: 신발만 다른 코디를 같이 내보내지 않음
   동점은 seed로 섞어서 깬다 (등록 순서로 깨면 같은 옷만 반복되고 원피스가 늘 뒤로 밀림)
5) 일교차: 귀가 전까지 가장 추운 때(later)가 지금보다 LATER_DROP 이상 추우면, 코디마다
   그때 입을 겉옷을 옷장에서 골라 '챙기세요'로 안내 (코디 자체는 지금 날씨 기준)

코디 구성: 원피스+신발 2벌 ~ 이너+상의+하의+겉옷+신발 5벌.
상의 겹쳐 입기는 LAYER_PAIRS 조합만, 유효 기온 LAYER_MAX_TEMP 미만에서만.
"""
from __future__ import annotations

import random
from itertools import product

from core.colors import color_score
from core.taxonomy import INCOMPATIBLE_PAIRS, LAYER_PAIRS, canonical_subcategory, rule_for

WEIGHTS = {"weather": 0.60, "rain": 0.25, "color": 0.15}

# 각 옷의 역할. 상의를 겹쳐 입으면 안에 입은 옷은 '이너'
# 비 적합도에서 각 옷의 비중 (실제로 젖는 건 신발·겉옷, 이너는 젖지 않음)
RAIN_ROLE_WEIGHT = {"신발": 0.50, "아우터": 0.30, "하의": 0.15, "상의": 0.05, "이너": 0.0, "원피스": 0.20}

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

# 유효 기온(℃) → 목표 보온값. 가장 얇은 조합(반팔+반바지+신발=0.85)이 30℃의 목표와 일치하도록 보정
# (최저 목표가 가장 얇은 조합보다 크면 더운 날 겉옷을 덧입히는 게 오히려 점수가 높아짐)
TARGET_CURVE = [
    (-10, 5.00), (-3, 4.60), (3, 4.10), (7, 3.60), (11, 3.10), (15, 2.50),
    (18, 2.00), (21, 1.55), (24, 1.15), (27, 1.00), (30, 0.85),
]

THERMAL_SHIFT = 3.0      # 체질 보정 최대 ±3℃
EXERCISE_SHIFT = 5.0     # 운동 중 체온 상승 (러닝 복장 가이드의 '실제보다 5~10℃ 따뜻하게 가정'을 보수적으로 적용)
RAIN_SHIFT = -1.0        # 20℃ 미만에서 비 오면 더 춥게 느낌
OUTER_REQUIRED_AT = 12.0  # 유효 기온이 이 이하면 겉옷 필수
LAYER_MAX_TEMP = 24.0    # 유효 기온이 이 이상이면 상의를 겹쳐 입히지 않음
WARMTH_TOLERANCE = 1.5   # 목표와 보온값 차이가 이만큼 나면 날씨 점수 0
LATER_DROP = 3.0         # 귀가 전 가장 추울 때의 유효 기온이 지금보다 이만큼 이상 낮을 때만 따로 안내
LATER_GAP = 0.35         # 그때 목표 보온값보다 이만큼 넘게 모자라면 '추울 수 있다'고 본다 (이유 문구 기준과 같음)
MAX_COMBOS = 60000

CAT_LABEL = {"상의": "상의", "하의": "하의", "원피스": "원피스", "아우터": "겉옷", "신발": "신발"}


# ---------------------------------------------------------------- 기온 / 보온
def effective_temperature(temperature, apparent_temperature, rain, purpose,
                          cold_sensitivity, heat_sensitivity):
    """(유효 기온, [(설명, 보정값)]). 체감온도에는 이미 바람·습도가 반영돼 있어 다시 더하지 않는다."""
    base = apparent_temperature if apparent_temperature is not None else temperature
    adj = []
    cold, heat = float(cold_sensitivity or 0), float(heat_sensitivity or 0)
    if cold:
        adj.append(("추위를 타는 체질", -THERMAL_SHIFT * cold))
    if heat:
        adj.append(("더위를 타는 체질", THERMAL_SHIFT * heat))
    if purpose == "운동":
        adj.append(("운동 중 체온 상승", EXERCISE_SHIFT))
    if rain and temperature < 20:
        adj.append(("비", RAIN_SHIFT))
    return base + sum(d for _, d in adj), adj


def target_warmth(eff):
    pts = TARGET_CURVE
    if eff <= pts[0][0]:
        return pts[0][1]
    if eff >= pts[-1][0]:
        return pts[-1][1]
    for (t0, w0), (t1, w1) in zip(pts, pts[1:]):
        if t0 <= eff <= t1:
            return w0 + (w1 - w0) * (eff - t0) / (t1 - t0)
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
    return sum(WARMTH_CONTRIB.get(r, [0] * 5)[_level(p) - 1] for p, r in zip(pieces, roles(pieces)))


# ---------------------------------------------------------------- 필터


def _purpose_ok(item, purpose):
    r = rule_for(item.get("subcategory"))
    return r is None or purpose not in r["blocked_purposes"]


def _temp_ok(item, eff):
    r = rule_for(item.get("subcategory"))
    return r is None or r["min_temp"] <= eff <= r["max_temp"]


def _josa(word, with_final="이", without_final="가"):
    ch = word[-1]
    if "가" <= ch <= "힣":
        return with_final if (ord(ch) - 0xAC00) % 28 else without_final
    return with_final


def _build_pool(items, cat, purpose, eff, rain, warnings):
    """카테고리 하나의 후보 목록. 비면 비/기온/목적 순으로 이 카테고리만 완화."""
    all_items = [x for x in items if x["category"] == cat]
    if not all_items:
        return []
    label = CAT_LABEL[cat]
    checks = [
        ("purpose", lambda x: _purpose_ok(x, purpose)),
        ("temp", lambda x: _temp_ok(x, eff)),
    ]
    if cat == "신발" and rain:
        checks.append(("rain", lambda x: bool(x["rain_ok"])))

    def run(active):
        return [x for x in all_items if all(fn(x) for name, fn in checks if name in active)]

    active = {name for name, _ in checks}
    pool = run(active)
    for drop in ("rain", "temp", "purpose"):
        if pool or drop not in active:
            continue
        active.discard(drop)
        pool = run(active)
        if pool and drop == "rain":
            names = ", ".join(x["name"] for x in pool if not x["rain_ok"])
            warnings.append(f"조건에 맞는 신발 중 비에 강한 것이 없어 비에 약한 신발({names})도 포함했어요. 젖지 않게 주의하세요.")
        elif pool and drop == "temp":
            warnings.append(f"체감 {eff:.0f}℃에 맞는 {label}{_josa(label)} 옷장에 없어 기온 기준을 완화했어요.")
        elif pool and drop == "purpose":
            warnings.append(f"'{purpose}'에 맞는 {label}{_josa(label)} 옷장에 없어 목적 기준을 완화했어요.")
    return pool


def _pairs_ok(pieces):
    subs = [_sub(p) for p in pieces if _sub(p)]
    for i in range(len(subs)):
        for j in range(i + 1, len(subs)):
            if frozenset((subs[i], subs[j])) in INCOMPATIBLE_PAIRS:
                return False
    return True


def outfit_key(pieces):
    return frozenset(int(p["id"]) for p in pieces)


def _top_units(tops, eff):
    """상의 한 벌, 또는 LAYER_PAIRS에 있는 두 벌 겹쳐 입기 [안, 위]."""
    units = [[t] for t in tops]
    if eff < LAYER_MAX_TEMP:
        units += [[a, b] for a in tops for b in tops if (_sub(a), _sub(b)) in LAYER_PAIRS]
    return units


def _prune(pools, eff):
    """조합 수가 MAX_COMBOS를 넘으면 착용 기온 범위의 가운데가 오늘 기온에서 먼 옷부터 덜어낸다 (id 순서 편향 없음)."""
    def pre(x):
        r = rule_for(x.get("subcategory"))
        if r is None:
            return 0.0
        mid = (max(r["min_temp"], -15) + min(r["max_temp"], 35)) / 2
        return -abs(eff - mid)

    def size():
        sep = len(_top_units(pools["상의"], eff)) * len(pools["하의"])
        return (sep + len(pools["원피스"])) * max(1, len(pools["신발"])) * (len(pools["아우터"]) + 1)

    while size() > MAX_COMBOS:
        cat = max(("상의", "하의", "원피스", "아우터", "신발"), key=lambda c: len(pools[c]))
        if len(pools[cat]) <= 3:
            break
        pools[cat] = sorted(pools[cat], key=pre, reverse=True)[:-1]
    return pools


# ---------------------------------------------------------------- 점수
def _weighted(pieces, role_weight, value_fn):
    rs = roles(pieces)
    tw = sum(role_weight.get(r, 0.2) for r in rs)
    return sum(role_weight.get(r, 0.2) * value_fn(p) for p, r in zip(pieces, rs)) / tw


def _score(pieces, ctx):
    eff, target, rain = ctx["eff"], ctx["target"], ctx["rain"]
    actual = outfit_warmth(pieces)
    weather_score = max(0.0, 1.0 - abs(actual - target) / WARMTH_TOLERANCE)

    has_outer = any(p["category"] == "아우터" for p in pieces)
    if rain:
        rw = dict(RAIN_ROLE_WEIGHT)
        if not has_outer:
            rw["상의"] = 0.30  # 겉옷이 없으면 상의가 그대로 젖음
        rain_score = _weighted(pieces, rw, lambda p: 1.0 if p["rain_ok"] else 0.2)
    else:
        rain_score = 1.0

    c_score, c_reason = color_score(pieces)

    w = WEIGHTS
    final = w["weather"] * weather_score + w["rain"] * rain_score + w["color"] * c_score

    # 이유 문구는 실제 계산 결과에서 만든다
    reasons = []
    rs = roles(pieces)
    if "이너" in rs:
        inner, over = pieces[rs.index("이너")], pieces[rs.index("상의")]
        reasons.append(f"{inner['name']} 위에 {over['name']}{_josa(over['name'], '을', '를')} 겹쳐 입는 레이어드 코디예요.")
    d = actual - target
    if abs(d) <= 0.35:
        reasons.append(f"체감 {eff:.0f}℃ 기준으로 두께가 잘 맞습니다.")
    elif d < 0:
        reasons.append(f"체감 {eff:.0f}℃ 기준으로 조금 서늘할 수 있어요. 안에 한 겹 더 입는 걸 권장해요.")
    else:
        reasons.append(f"체감 {eff:.0f}℃ 기준으로 조금 더울 수 있어요."
                       + (" 겉옷을 벗어 조절하세요." if has_outer else ""))
    if rain:
        weak = [p["name"] for p in pieces if p["category"] in ("신발", "아우터") and not p["rain_ok"]]
        if weak:
            reasons.append(f"비에 약한 {', '.join(weak)}{_josa(weak[-1])} 포함돼 있어요. 젖지 않게 주의하세요.")
        else:
            reasons.append("신발·겉옷이 비에 강한 조합입니다." if has_outer else "신발이 비에 강한 조합입니다. 우산을 챙기세요.")
    reasons.append(c_reason)

    components = {
        "weather_score": round(weather_score, 4),
        "rain_score": round(rain_score, 4),
        "color_score": round(c_score, 4),
        "target_warmth": round(target, 3),
        "actual_warmth": round(actual, 3),
        "effective_temp": round(eff, 1),
    }
    return round(max(0.0, min(1.0, final)), 4), reasons, components


def _context(temperature, apparent_temperature, rain, purpose, cold, heat, later=None):
    eff, adj = effective_temperature(temperature, apparent_temperature, rain, purpose, cold, heat)
    ctx = {
        "eff": eff, "adjustments": adj, "target": target_warmth(eff), "rain": bool(rain),
        "purpose": purpose,
        "base_temp": apparent_temperature if apparent_temperature is not None else temperature,
        "later": None,
    }
    if later:
        l_app = later.get("apparent_temperature")
        l_eff, _ = effective_temperature(later["temperature"], l_app, rain, purpose, cold, heat)
        ctx["later"] = {"label": later.get("label") or "저녁", "eff": l_eff, "target": target_warmth(l_eff),
                        "base_temp": l_app if l_app is not None else later["temperature"]}
    return ctx


def _later_advice(pieces, ctx, outers):
    """(챙길 겉옷 or None, 안내 문구) 또는 None. 귀가 전 가장 추울 때 이 코디로 모자라면 옷장에서 겉옷을 고른다."""
    lt = ctx["later"]
    if not lt or lt["eff"] > ctx["eff"] - LATER_DROP:
        return None
    has_outer = any(p["category"] == "아우터" for p in pieces)
    short = lt["target"] - outfit_warmth(pieces) > LATER_GAP
    if not (short or (not has_outer and lt["eff"] <= OUTER_REQUIRED_AT)):
        return None
    when = f"{lt['label']}쯤 체감 {lt['eff']:.0f}℃까지 내려가요."

    def fit(base, o):  # 그때 목표 보온값에 가까울수록, 같으면 색이 잘 어울릴수록, 그래도 같으면 id 순
        return (round(abs(outfit_warmth(base + [o]) - lt["target"]), 6), -color_score(base + [o])[0], int(o["id"]))

    if has_outer:  # 입고 있는 겉옷보다 두껍고 그때 더 잘 맞는 겉옷이 있으면 그걸 입고 나가라고 안내
        cur = next(p for p in pieces if p["category"] == "아우터")
        rest = [p for p in pieces if p is not cur]
        gap = round(abs(outfit_warmth(pieces) - lt["target"]), 6)
        better = [o for o in outers if int(o["id"]) != int(cur["id"]) and _level(o) > _level(cur)
                  and _purpose_ok(o, ctx["purpose"]) and _temp_ok(o, lt["eff"]) and _pairs_ok(rest + [o])
                  and fit(rest, o)[0] < gap]
        if not better:
            return None, f"{when} {cur['name']}만으로는 조금 추울 수 있으니 안에 한 겹 더 챙기세요."
        best = min(better, key=lambda o: fit(rest, o))
        return best, (f"{when} {cur['name']}보다 {best['name']}{_josa(best['name'])} 더 따뜻해요. "
                      f"늦게까지 밖에 있으면 {best['name']}{_josa(best['name'], '을', '를')} 입고 나가세요.")
    ok = [o for o in outers if _purpose_ok(o, ctx["purpose"]) and _pairs_ok(pieces + [o])]
    cands = [o for o in ok if _temp_ok(o, lt["eff"])] or ok
    if not cands:
        return None, f"{when} 챙길 만한 겉옷이 옷장에 없어요."
    best = min(cands, key=lambda o: fit(pieces, o))
    return best, f"{when} {best['name']}{_josa(best['name'], '을', '를')} 챙기세요."


def _attach_later(result, ctx, outers):
    """결과에 carry(챙길 겉옷)를 달고, 안내 문구를 기온 이유 바로 뒤에 넣는다."""
    result["carry"] = None
    if not ctx["later"]:
        return
    result["components"]["later_effective_temp"] = round(ctx["later"]["eff"], 1)
    advice = _later_advice(result["pieces"], ctx, outers)
    if advice:
        result["carry"], text = advice
        at = next((i + 1 for i, r in enumerate(result["reasons"]) if r.startswith("체감 ")), len(result["reasons"]))
        result["reasons"].insert(at, text)


def score_outfit(pieces, temperature, apparent_temperature, humidity, precipitation, rain, wind_speed,
                 purpose, cold_sensitivity, heat_sensitivity):
    """단일 코디 점수."""
    ctx = _context(temperature, apparent_temperature, rain, purpose, cold_sensitivity, heat_sensitivity)
    return _score(pieces, ctx)


# ---------------------------------------------------------------- 다양화
def _core(pieces):
    return frozenset(int(p["id"]) for p in pieces if p["category"] in ("상의", "하의", "원피스"))


def _non_shoe(pieces):
    return frozenset(int(p["id"]) for p in pieces if p["category"] != "신발")


def _pick_diverse(scored, k):
    """점수 순으로 고르되, 비슷한 코디는 뒤로 미룬다.
    1차: 상의·하의(원피스)가 모두 다름 → 2차: 겹치는 옷 1개 이하 → 3차: 상·하의 구성이 다름
    → 4차: 신발만 다른 코디는 끝까지 제외.
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
    picked.sort(key=lambda r: r["score"], reverse=True)
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
    eff = ctx["eff"]
    warnings = []
    usable = [x for x in items if x.get("category") in WARMTH_CONTRIB]

    pools = {c: [] for c in ("상의", "하의", "원피스", "아우터", "신발")}
    pools["신발"] = _build_pool(usable, "신발", purpose, eff, rain, warnings)
    # 상·하의 경로나 원피스 경로 중 하나만 살아 있으면 된다 → 둘 다 막혔을 때만 완화
    strict = {c: [x for x in usable if x["category"] == c and _purpose_ok(x, purpose) and _temp_ok(x, eff)]
              for c in ("상의", "하의", "원피스")}
    if (strict["상의"] and strict["하의"]) or strict["원피스"]:
        separates_ok = bool(strict["상의"] and strict["하의"])
        pools["상의"] = strict["상의"] if separates_ok else []
        pools["하의"] = strict["하의"] if separates_ok else []
        pools["원피스"] = strict["원피스"]
    else:
        # 상·하의 경로를 먼저 완화해 보고, 그래도 안 되면 원피스를 완화 (운동에 원피스가 섞이는 것 방지)
        tmp = []
        top = _build_pool(usable, "상의", purpose, eff, rain, tmp)
        bottom = _build_pool(usable, "하의", purpose, eff, rain, tmp)
        if top and bottom:
            pools["상의"], pools["하의"] = top, bottom
            warnings.extend(tmp)
        else:
            pools["원피스"] = _build_pool(usable, "원피스", purpose, eff, rain, warnings)

    outer_required = eff <= OUTER_REQUIRED_AT
    strict_outer = [x for x in usable if x["category"] == "아우터" and _purpose_ok(x, purpose) and _temp_ok(x, eff)]
    if outer_required and not strict_outer:
        strict_outer = _build_pool(usable, "아우터", purpose, eff, rain, warnings)
        if not strict_outer:
            warnings.append(f"체감 {eff:.0f}℃라 겉옷이 필요하지만 옷장에 맞는 겉옷이 없어요.")
            outer_required = False
    pools["아우터"] = strict_outer
    if eff <= 5 and not any(_level(x) >= 4 for x in strict_outer):
        warnings.append(f"체감 {eff:.0f}℃인데 두꺼운 겉옷(코트·패딩)이 옷장에 없어 보온이 부족할 수 있어요.")

    if not pools["신발"] or not ((pools["상의"] and pools["하의"]) or pools["원피스"]):
        out = {"results": [], "warnings": warnings + ["추천에 필요한 옷 조합(상의+하의+신발 또는 원피스+신발)이 없습니다."],
               "context": ctx, "n_combos": 0}
        if must_include is not None:
            out["combo_keys"] = []
        return out

    if must_include is None:  # 특정 옷을 꼭 넣을 때는 덜어내지 않는다 (그 옷이 빠질 수 있어서)
        pools = _prune(pools, eff)
    outers = pools["아우터"] if outer_required else [None] + pools["아우터"]

    def combos():
        for top, bottom, outer, shoe in product(_top_units(pools["상의"], eff), pools["하의"], outers, pools["신발"]):
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

    random.Random(seed).shuffle(kept)  # 이후 안정 정렬 → 동점끼리는 섞인 순서 유지
    scored = []
    for pieces in kept:
        score, reasons, components = _score(pieces, ctx)
        scored.append({"score": score, "pieces": pieces, "reasons": reasons, "components": components})
    scored.sort(key=lambda r: r["score"], reverse=True)
    results = _pick_diverse(scored, top_k)
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
    pct = lambda k: f"{WEIGHTS[k]*100:.0f}%"
    return [
        {"항목": "날씨 적합도", "점수": c["weather_score"], "가중치": pct("weather")},
        {"항목": "비/강수 적합도", "점수": c["rain_score"], "가중치": pct("rain")},
        {"항목": "색상 조합", "점수": c.get("color_score", 0), "가중치": pct("color")},
    ]
