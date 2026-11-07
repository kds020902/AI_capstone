"""비교 온도와 옷 한 벌의 후보 자격, 젖음 부담.

추천(core/recommender.py)과 사기 전에 맞춰보기(core/shopping.py)가 이 함수들을 같이 쓴다.
계절 분류(core/taxonomy.py seasons_for)도 같은 기온 비교(temp_fits)를 쓴다.
웹 체험판(web/src/closet.js)에는 같은 이름의 함수가 있고 tests/test_web_parity.py 가 결과를 비교한다.

온도 세 가지
- T_env    실제 체감온도 (Open-Meteo apparent_temperature, 수동 입력이면 그 값)
- T_filter 추천 기준 온도 = T_env (+5℃ 실외 운동). 옷의 착용 가능 범위(min/max)와 비교하는 값
- T_pref   두께 취향 온도 = T_filter + 체질(±3℃) − 쌀쌀한 비(최대 1℃). 목표 두께를 정할 때만 쓴다
  → 체질은 '입을 수 있는 옷'을 바꾸지 않고, 그 안에서 더 얇은/두꺼운 쪽을 고르게 한다
"""
from __future__ import annotations

from core.taxonomy import rule_for, temp_fits

EXERCISE_SHIFT = 5.0     # 실외 운동 중 체온 상승 (러닝 복장 가이드의 '실제보다 5~10℃ 따뜻하게'를 보수적으로)
THERMAL_SHIFT = 3.0      # 체질 보정 최대 ±3℃ (두께 취향에만)
RAIN_CHILL = 1.0         # 쌀쌀한 비에서 두께 취향을 최대 이만큼 낮춘다
RAIN_CHILL_WARM = 22.0   # 체감 이 온도 이상인 비는 춥게 보정하지 않고,
RAIN_CHILL_SPAN = 4.0    # 이만큼 낮아질수록(18℃ 이하면 전부) 보정을 키운다

# 후보 자격 단계: (목적 완화 허용, 기온 완화 허용). 앞 단계로 코디가 하나라도 나오면 거기서 멈춘다
TIERS = ((False, False), (True, False), (False, True), (True, True))


def temperature_context(temperature, apparent_temperature, rain, purpose, cold=0.0, heat=0.0):
    t_env = apparent_temperature if apparent_temperature is not None else temperature
    t_filter = t_env + (EXERCISE_SHIFT if purpose == "운동" else 0.0)
    cold, heat = float(cold or 0), float(heat or 0)
    chill = min(1.0, max(0.0, (RAIN_CHILL_WARM - t_env) / RAIN_CHILL_SPAN)) if rain else 0.0
    adj = []
    if cold:
        adj.append(("추위를 타는 체질", -THERMAL_SHIFT * cold))
    if heat:
        adj.append(("더위를 타는 체질", THERMAL_SHIFT * heat))
    if chill:
        adj.append(("쌀쌀한 비", -RAIN_CHILL * chill))
    t_pref = t_filter
    for _, d in adj:   # sum()은 파이썬 3.12부터 보정 덧셈이라 웹(JS)과 같게 차례로 더한다
        t_pref += d
    return {"t_env": t_env, "t_filter": t_filter, "t_pref": t_pref,
            "rain": bool(rain), "purpose": purpose, "adjustments": adj}


def temp_ok(item, ctx):
    """추천 기준 온도가 착용 가능 범위 안이면 True. 비 오는 날 바람막이처럼 비를 막는 겉옷은
    실제 체감으로 봐도 범위 안이면 True (비 오는 날 운동하면 추천 기준이 올라 범위를 넘어도 입을 수 있게)."""
    r = rule_for(item.get("subcategory"))
    if r is None:
        return True
    return temp_fits(r, ctx["t_filter"]) or (ctx["rain"] and r["weather_shell"] and temp_fits(r, ctx["t_env"]))


def item_flags(item, ctx):
    """None = 이 목적엔 절대 추천 안 함. 아니면 (목적 완화가 필요한가, 기온 완화가 필요한가)."""
    r = rule_for(item.get("subcategory"))
    if r is None:                       # 규칙표에 없는 옛 옷은 막지 않는다
        return (False, False)
    if ctx["purpose"] in r["blocked_purposes"]:
        return None
    return (ctx["purpose"] in r["fallback_purposes"], not temp_ok(item, ctx))


def allowed(flags, tier):
    if flags is None:
        return False
    purpose_relax, temp_relax = TIERS[tier]
    return (purpose_relax or not flags[0]) and (temp_relax or not flags[1])


def wearable(item, ctx):
    """완화 없이 바로 쓸 수 있는 옷인가 (목적·기온 둘 다 맞음)."""
    return allowed(item_flags(item, ctx), 0)


def wet_burden(item):
    """비에 젖었을 때 불편함 0 / 0.5 / 1. 규칙표 값에서 시작하고, 사용자가 '비 오는 날 부담이 적음'을
    규칙표 기본값과 다르게 바꿨으면 그 체크와 맞는 가장 가까운 단계로 옮긴다 (사용자 판단이 항상 이김)."""
    rain_ok = bool(item.get("rain_ok"))
    r = rule_for(item.get("subcategory"))
    if r is None:
        return 0.0 if rain_ok else 1.0
    w = r["wet_burden"]
    if rain_ok != r["default_rain_ok"]:
        if rain_ok and w > 0.5:
            w = 0.5
        elif not rain_ok and w <= 0.5:
            w = 1.0
    return w
