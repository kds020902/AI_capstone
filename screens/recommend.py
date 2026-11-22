"""오늘의 추천: 날씨(자동·수동)와 외출 목적으로 TOP 3 코디를 만들고 저장·피드백을 받는다."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date

import pandas as pd
import streamlit as st

from core import weather
from core.recommender import explain_score_breakdown, recommend
from core.taxonomy import MAIN_CATEGORIES, PURPOSES
from core.weather import CITY_PRESETS, RETURN_HOURS, coldest_until
from screens.common import THERMAL_FEEDBACK, go_to_page

# 날씨를 불러오기 전 기본값 (수동 입력 슬라이더의 처음 값과 같다)
DEFAULT_WEATHER = {"temperature": 22.0, "humidity": 70.0, "apparent": 22.0, "precipitation": 0.0, "rain": False,
                   "wind": 8.0, "source": "manual", "later": None}
# 세부 설정의 처음 값. 바꾸면 접힌 칸 바깥에 '적용 중' 줄로 알린다.
DEFAULT_RETURN_HOUR = 22
MANUAL_DETAIL_DEFAULTS = {"humidity": 70.0, "precipitation": 0.0, "wind": 8.0}

# 화면 배치: 왼쪽 조건 : 오른쪽 결과 = 1 : 2 (docs/ui-guide.md)
LAYOUT = [1, 2]
# 오른쪽 결과 목록만 세로로 스크롤한다. 높이는 화면 높이에 맞춰 줄고(고정 높이 st.container 는 화면 크기를 몰라서 안 씀),
# 결과가 짧으면 스크롤이 생기지 않는다. 목록 끝에 닿아도 페이지가 따라 움직이지 않게 스크롤 전파를 막는다.
# 화면 위쪽(제목·적용 조건·'추천 결과' 제목)이 약 330px이라 그만큼 빼고, 너무 작은 화면에서도 420px은 남긴다.
RESULTS_KEY = "rec_results"
RESULTS_SCROLL_CSS = f"""<style>
.st-key-{RESULTS_KEY} {{
  max-height: max(420px, calc(100vh - 350px));
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-right: 0.5rem;
}}
</style>"""


def render(app):
    st.title("오늘의 추천")
    user = app.need_user()
    items = app.active_items(user["id"])
    missing = missing_categories(items)
    if missing:
        _not_enough(app, user, missing)
        return

    applied = st.container()                  # 제목 아래 '지금 적용 조건' (왼쪽 입력을 읽은 뒤 채운다)
    left, right = st.columns(LAYOUT, gap="medium")
    with left:
        st.subheader("추천 조건")
        w, status, purpose = _conditions()
        clicked = st.button("코디 추천받기", type="primary", width="stretch")
    with applied:
        _applied_conditions(w, status)
    if clicked:
        _make_recommendation(app, user, items, w, purpose)
    with right:
        st.subheader("추천 결과")
        st.html(RESULTS_SCROLL_CSS)
        with st.container(key=RESULTS_KEY):      # 결과 목록만 따로 스크롤 (제목은 밖에 고정)
            _results(app, user, purpose, status)


# ---------------------------------------------------------------- 옷이 부족할 때
NEEDED = "상의 + 하의 + 신발, 또는 원피스 + 신발"


def missing_categories(items):
    """추천에 필요한 조합(상의+하의+신발 / 원피스+신발)에서 빠진 것. 다 있으면 []."""
    cats = {x["category"] for x in items}
    if ("상의" in cats and "하의" in cats and "신발" in cats) or ("원피스" in cats and "신발" in cats):
        return []
    out = []
    if not (("상의" in cats and "하의" in cats) or "원피스" in cats):
        lack = [c for c in ("상의", "하의") if c not in cats]
        out.append(f"{'·'.join(lack)} (또는 원피스)")
    if "신발" not in cats:
        out.append("신발")
    return out


def _not_enough(app, user, missing):
    st.warning(f"추천에 필요한 옷이 부족해요. 빠진 옷: **{', '.join(missing)}**")
    st.markdown(f"추천하려면 추천에 쓰는 옷 중에 **{NEEDED}** 조합이 있어야 해요.")
    active = app.active_items(user["id"])
    counts = {c: sum(1 for x in active if x["category"] == c) for c in MAIN_CATEGORIES}
    have = ", ".join(f"{c} {n}벌" for c, n in counts.items() if n) or "없음"
    note = f"지금 추천에 쓰는 옷: {have}"
    if app.inactive_items(user["id"]):
        note += f" · 넣어 둔 옷 {len(app.inactive_items(user['id']))}벌은 '내 옷장'에서 꺼내면 추천에 쓸 수 있어요."
    st.caption(note)
    go_to_page("내 옷장에서 옷 넣기", "내 옷장", key="rec_go_wardrobe", primary=True)


# ---------------------------------------------------------------- 왼쪽: 추천 조건
def _conditions():
    """날씨 설정 → 외출 목적 → 저녁 대비·세부 설정. 반환: (날씨 값, 날씨 상태, 외출 목적)."""
    mode = st.radio("날씨", ["자동 조회", "수동 입력"], horizontal=True)
    if mode == "자동 조회":
        city, data, cached = _auto_weather_inputs()
    else:
        w = _manual_main()
    purpose = st.selectbox("외출 목적", PURPOSES)
    changed = []
    with st.expander("저녁 대비 · 세부 설정"):
        if mode == "자동 조회":
            return_hour = st.select_slider("귀가 예정", RETURN_HOURS, value=DEFAULT_RETURN_HOUR,
                                           format_func=_hour_label)
            st.caption("귀가 전까지 가장 추운 때를 예보에서 찾아 저녁 대비 겉옷을 알려 줘요.")
            if return_hour != DEFAULT_RETURN_HOUR:
                changed.append(f"귀가 {_hour_label(return_hour)}")
        else:
            changed = _manual_details(w)
    if mode == "자동 조회":
        w, status = _auto_weather(city, data, cached, return_hour)
    else:
        status = {"kind": "manual"}
    if changed:
        st.caption("적용 중인 세부 설정: " + ", ".join(changed))
    return w, status, purpose


def _hour_label(h):
    return "자정" if h == 24 else f"{h}시"


def _auto_weather_inputs():
    """도시 → [현재 날씨 불러오기]. 불러온 날씨는 도시와 함께 기억하고, 다른 도시 날씨는 쓰지 않는다."""
    city = st.selectbox("도시", list(CITY_PRESETS))
    if st.button("현재 날씨 불러오기", width="stretch"):
        lat, lon = CITY_PRESETS[city]
        st.session_state.weather_cache = {"city": city, "data": weather.get_current_weather(lat, lon)}
    cached = st.session_state.weather_cache
    data = cached["data"] if cached and cached["city"] == city else None
    return city, data, cached


def _auto_weather(city, data, cached, return_hour):
    """불러온 날씨(이 도시 것만)로 추천에 쓸 값을 만든다. 없거나 실패하면 기본값. 반환: (날씨 값, 상태)."""
    w = dict(DEFAULT_WEATHER)
    if data and data.get("ok"):
        w.update(temperature=data["temperature"], humidity=data["humidity"], apparent=data["apparent_temperature"],
                 precipitation=data["precipitation"], rain=data["rain"] > 0 or data["precipitation"] > 0,
                 wind=data["wind_speed"], source=data["source"], later=coldest_until(data, return_hour))
        return w, {"kind": "loaded", "city": city, "return_hour": return_hour}
    if data:
        return w, {"kind": "failed", "city": city, "message": data["message"]}
    if cached:
        return w, {"kind": "other_city", "city": city, "loaded_city": cached["city"]}
    return w, {"kind": "default", "city": city}


def _manual_main():
    """수동 입력에서 자주 바꾸는 값: 기온, 체감온도, 비."""
    w = {"temperature": st.slider("기온", -10.0, 40.0, 22.0, 0.5),
         "apparent": st.slider("체감온도", -15.0, 45.0, 22.0, 0.5), "source": "manual", "later": None}
    w["rain"] = st.checkbox("비가 옴")
    return w


def _manual_details(w):
    """수동 입력의 세부 값(습도·강수량·풍속)과 저녁 대비. 처음 값에서 바꾼 것을 돌려준다."""
    w["humidity"] = st.slider("습도", 10.0, 100.0, 70.0, 1.0)
    w["precipitation"] = st.number_input("강수량(mm)", 0.0, 100.0, 0.0, 0.5)
    w["wind"] = st.slider("풍속(km/h)", 0.0, 60.0, 8.0, 0.5)
    changed = []
    if st.checkbox("저녁 늦게까지 밖에 있어요"):
        later = st.slider("저녁 체감온도", -15.0, 45.0, max(-15.0, w["apparent"] - 6), 0.5)
        w["later"] = {"temperature": later, "apparent_temperature": later, "label": "저녁"}
        changed.append(f"저녁 체감 {later:.1f}℃")
    units = {"humidity": ("습도", "%", 0), "precipitation": ("강수량", "mm", 1), "wind": ("풍속", " km/h", 1)}
    for key, default in MANUAL_DETAIL_DEFAULTS.items():
        if w[key] != default:
            name, unit, digits = units[key]
            changed.append(f"{name} {w[key]:.{digits}f}{unit}")
    return changed


# ---------------------------------------------------------------- 위: 지금 적용 조건
def _applied_conditions(w, status):
    """제목 아래 한 줄: 지역 · 체감온도 · 비. 기본값·조회 실패는 이 줄에 붙여서 알린다."""
    kind = status["kind"]
    rain = "비 옴" if w["rain"] else "비 없음"
    if kind == "manual":
        st.markdown(f"**직접 입력한 날씨** · 체감 **{w['apparent']:.1f}℃** · **{rain}**")
        detail = f"기온 {w['temperature']:.1f}℃ · 습도 {w['humidity']:.0f}% · 풍속 {w['wind']:.1f} km/h"
        if w["later"]:
            detail += f" · 저녁 체감 {w['later']['apparent_temperature']:.1f}℃"
        st.caption(detail)
        return
    if kind == "loaded":
        st.markdown(f"**{status['city']}** · 체감 **{w['apparent']:.1f}℃** · **{rain}**")
        detail = (f"{w['source']} · 기온 {w['temperature']:.1f}℃ · 습도 {w['humidity']:.0f}% · "
                  f"풍속 {w['wind']:.1f} km/h")
        if w["later"]:
            detail += f" · 귀가 전 최저 체감 ({w['later']['label']}) {w['later']['apparent_temperature']:.1f}℃"
        st.caption(detail)
        return
    st.markdown(f"**{status['city']}** · 체감 **{w['apparent']:.1f}℃ (기본값)** · **{rain} (기본값)**")
    if kind == "failed":
        found = re.search(r"\(([^()]*)\)\s*$", status["message"])     # 실패 원인 (예: ConnectTimeout)
        st.warning("날씨를 불러오지 못해 기본값(22℃, 비 없음)으로 추천합니다. 다시 불러오거나 '수동 입력'을 써 주세요."
                   f" (원인: {found.group(1) if found else status['message']})")
    elif kind == "other_city":
        st.caption(f"{status['loaded_city']} 날씨를 불러온 상태예요. {status['city']} 날씨는 '현재 날씨 불러오기'를 "
                   "다시 눌러 주세요. 그 전에는 기본값(22℃, 비 없음)으로 추천합니다.")
    else:
        st.caption("날씨를 불러오기 전에는 기본값(22℃, 비 없음)으로 추천합니다. '현재 날씨 불러오기'를 눌러 주세요.")


# ---------------------------------------------------------------- 오른쪽: 추천 결과
def _results(app, user, purpose, status):
    results = st.session_state.recommendations
    meta = st.session_state.rec_meta or {}
    if results is None:
        st.info(_next_step(status))
        return
    ctx = meta.get("context")
    if ctx:
        _context_caption(ctx)
    for warning in meta.get("warnings", []):
        st.warning(warning)
    if not results:
        st.error("추천할 수 있는 코디가 없습니다. 위 안내의 옷을 옷장에 넣거나, 외출 목적을 바꿔 보세요.")
        go_to_page("내 옷장 확인하기", "내 옷장", key="rec_go_wardrobe_empty")
        return
    st.caption("점수 차이가 2점 안이면 색 조합이 더 잘 어울리는 코디를 앞에 둬요.")
    for rank, result in enumerate(results, 1):
        _result_card(app, user, rank, result, (ctx or {}).get("purpose", purpose), (ctx or {}).get("later"))


def _next_step(status):
    """추천 전, 결과 자리에 보여 줄 다음 행동 한 문장."""
    if status["kind"] in ("default", "other_city"):
        return "'현재 날씨 불러오기'를 누르고 외출 목적을 고른 뒤 '코디 추천받기'를 눌러 주세요."
    if status["kind"] == "failed":
        return "날씨를 다시 불러오거나 '수동 입력'으로 바꾼 뒤 '코디 추천받기'를 눌러 주세요."
    return "외출 목적을 확인하고 '코디 추천받기'를 누르면 여기에 코디 3개가 나와요."


def _make_recommendation(app, user, items, w, purpose):
    """추천을 만들어 세션·결과를 저장하고, 화면에 보여 줄 값을 st.session_state 에 둔다."""
    out = recommend(
        items, w["temperature"], w["apparent"], w["humidity"], w["precipitation"], w["rain"], w["wind"],
        purpose, float(user["cold_sensitivity"]), float(user["heat_sensitivity"]), 3,
        exclude_keys=app.store.disliked_item_sets(user["id"]),
        seed=f"{user['id']}-{date.today().isoformat()}",  # 같은 날엔 같은 결과, 날마다 동점 코디가 바뀜
        later=w["later"],
    )
    fields = (purpose, w["temperature"], w["apparent"], w["humidity"], w["precipitation"], w["rain"], w["wind"],
              w["source"])
    key = request_key(fields, out["results"])
    sid, rids = app.store.save_recommendation_session(user["id"], _request_id(key), *fields, out["results"])
    st.session_state.rec_pending.pop(key, None)    # 성공한 요청만 지운다 → 같은 추천을 또 누르면 새 세션
    for result, rid in zip(out["results"], rids):
        result["recommendation_id"] = rid
    st.session_state.recommendations = out["results"]
    st.session_state.recommendation_session_id = sid
    st.session_state.rec_meta = {"warnings": out["warnings"], "context": out["context"]}


def request_key(fields, results):
    """저장할 본문 전체(세션 값 + 결과마다 점수·옷·이유·근거를 저장소와 같은 방식으로 직렬화)의 지문.
    점수·옷 id만 같고 옷 이름·이유(저녁 안내 등)·근거가 다르면 다른 요청이다."""
    body = [list(fields), [[r["score"], json.dumps(r["pieces"], ensure_ascii=False),
                            json.dumps(r["reasons"], ensure_ascii=False),
                            json.dumps(r["components"], ensure_ascii=False)] for r in results]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, default=str).encode()).hexdigest()


REC_PENDING_MAX = 200   # 결과를 모르는 추천 요청을 세션에서 기억하는 수 (실패할 때만 늘고, 넘치면 가장 오래된 것부터 잊음)


def _request_id(key):
    """추천 저장 요청 id. 저장이 오류로 끝나 결과를 모르는 요청은 본문 지문별로 세션이 끝날 때까지 기억하고, 같은 본문을
    다시 저장하면 같은 id를 쓴다 (사실은 저장이 됐어도 응답만 못 받은 경우 세션이 두 번 생기지 않게. 그사이 다른 추천이
    성공했거나 A→B→A로 바꿔도 A의 id). 성공하면 그 요청만 잊는다. 본문이 하나라도 다르면 새 id라서, DB가 돌려주는
    추천 id는 항상 화면에 보이는 내용과 같은 기록이다."""
    pending = st.session_state.get("rec_pending")
    if pending is None:
        pending = st.session_state.rec_pending = {}
    if key not in pending:
        while len(pending) >= REC_PENDING_MAX:
            pending.pop(next(iter(pending)))
        pending[key] = str(uuid.uuid4())
    return pending[key]


def _context_caption(ctx):
    """이 추천을 만든 조건: '외출 목적 · 실제 체감 · 추천 기준' 줄 (운동 보정, 두께에 반영한 체질·비, 저녁 기온)."""
    line = f"{ctx['purpose']} · 실제 체감 {ctx['t_env']:.1f}℃ · 추천 기준 **{ctx['t_filter']:.1f}℃**"
    if ctx["purpose"] == "운동":
        line += " (실외 운동 +5℃)"
    if ctx["adjustments"]:
        adj = ", ".join(f"{label} {delta:+.1f}℃" for label, delta in ctx["adjustments"])
        line += f" · 두께는 {adj}를 반영해 {ctx['t_pref']:.1f}℃에 맞춰 골라요"
    st.markdown(line)
    if ctx.get("later"):
        lt = ctx["later"]
        st.caption(f"{lt['label']}: 체감 {lt['t_env']:.1f}℃ (지금보다 {ctx['t_env'] - lt['t_env']:.1f}℃ 낮음)")


def split_later_reason(reasons, later):
    """추천 이유에서 저녁 대비 안내(추천 규칙이 '{시각}쯤 체감 …'으로 넣은 문장)를 떼어 낸다. → (나머지 이유, 저녁 안내)"""
    if not later:
        return list(reasons), None
    head = f"{later['label']}쯤 체감 "
    rest = [r for r in reasons if not r.startswith(head)]
    advice = next((r for r in reasons if r.startswith(head)), None)
    return rest, advice


def _result_card(app, user, rank, result, purpose, later=None):
    """카드 순서: 순위·옷 조합 → 추천 이유 → 저녁 대비(해당 시) → 저장·좋아요·별로 → 점수 근거·착용 후 피드백."""
    rid = result["recommendation_id"]
    reasons, advice = split_later_reason(result["reasons"], later)
    with st.container(border=True):
        st.markdown(f"##### {rank}위 · {result['score']*100:.1f}%")
        st.markdown("  \n".join(f"{p['category']} · **{p['name']}**" for p in result["pieces"]))
        if reasons:
            st.markdown("\n".join(f"- {r}" for r in reasons))
        if result.get("carry") or advice:
            head = f"**저녁 대비 겉옷: {result['carry']['name']}**  \n" if result.get("carry") else "**저녁 대비**  \n"
            st.info(head + (advice or ""), icon="🧥")
        with st.container(horizontal=True, gap="small"):
            save = st.button("이 코디 저장", key=f"s_{rid}")
            like = st.button("👍 좋아요", key=f"l_{rid}")
            dislike = st.button("👎 별로", key=f"d_{rid}")
        if save:
            app.store.save_outfit(user["id"], rid)
            app.store.complete_recommendation_session(st.session_state.recommendation_session_id)
            st.success("코디를 저장했습니다. 메뉴의 '저장 코디'에서 다시 볼 수 있어요.")
        if like:
            app.store.upsert_feedback(user["id"], rid, rating="like")
            st.toast("좋아요를 기록했습니다.")
        if dislike:
            app.store.upsert_feedback(user["id"], rid, rating="dislike")
            st.toast("다음 추천부터 이 조합은 제외합니다.")
        with st.expander("점수 근거"):
            st.dataframe(pd.DataFrame(explain_score_breakdown(result["components"], purpose)),
                         hide_index=True, width="stretch")
        with st.expander("착용 후 피드백"):
            _feedback_form(app, user, rid)


def _feedback_form(app, user, rid):
    thermal = st.radio("체감", list(THERMAL_FEEDBACK), index=0, horizontal=True,
                       format_func=lambda x: THERMAL_FEEDBACK[x], key=f"t_{rid}")
    rating = st.slider("코디 만족도", 1, 5, 3, key=f"r_{rid}")
    worn = st.checkbox("실제로 착용함", key=f"w_{rid}")
    if st.button("피드백 저장", key=f"f_{rid}"):
        app.store.upsert_feedback(user["id"], rid, worn=worn, thermal_feedback=thermal, satisfaction=rating)
        st.success("저장했습니다.")
