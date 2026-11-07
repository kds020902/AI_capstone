"""오늘의 추천: 날씨(자동·수동)와 외출 목적으로 TOP 3 코디를 만들고 저장·피드백을 받는다."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core.recommender import explain_score_breakdown, recommend
from core.taxonomy import PURPOSES
from core import weather
from core.weather import CITY_PRESETS, RETURN_HOURS, coldest_until
from screens.common import THERMAL_FEEDBACK, tryon_button


def render(app):
    st.title("오늘의 추천")
    user = app.need_user()
    items = app.active_items(user["id"])
    cats = {x["category"] for x in items}
    if not (("상의" in cats and "하의" in cats and "신발" in cats) or ("원피스" in cats and "신발" in cats)):
        st.warning("상의+하의+신발 또는 원피스+신발 조합이 필요합니다. 내 옷장에 옷을 더 넣거나 넣어 둔 옷을 꺼내 주세요.")
        return

    mode = st.radio("날씨", ["자동 조회", "수동 입력"], horizontal=True)
    temperature, humidity, apparent, precipitation, rain, wind, source = 22.0, 70.0, 22.0, 0.0, False, 8.0, "manual"
    later = None  # 귀가 전 가장 추운 때 → 추천마다 '챙길 겉옷' 안내

    if mode == "자동 조회":
        c1, c2 = st.columns(2)
        city = c1.selectbox("도시", list(CITY_PRESETS))
        return_hour = c2.select_slider("귀가 예정", RETURN_HOURS, value=22,
                                       format_func=lambda h: "자정" if h == 24 else f"{h}시")
        lat, lon = CITY_PRESETS[city]
        if st.button("현재 날씨 불러오기"):
            st.session_state.weather_cache = {"city": city, "data": weather.get_current_weather(lat, lon)}
        cached = st.session_state.weather_cache
        data = cached["data"] if cached and cached["city"] == city else None  # 다른 도시 날씨는 쓰지 않는다
        if data and data.get("ok"):
            temperature = data["temperature"]
            humidity = data["humidity"]
            apparent = data["apparent_temperature"]
            precipitation = data["precipitation"]
            rain = data["rain"] > 0 or precipitation > 0
            wind = data["wind_speed"]
            source = data["source"]
            later = coldest_until(data, return_hour)
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("기온", f"{temperature:.1f}℃")
            c2.metric("체감", f"{apparent:.1f}℃")
            c3.metric("습도", f"{humidity:.0f}%")
            c4.metric("풍속", f"{wind:.1f} km/h")
            if later:
                c5.metric(f"귀가 전 최저 체감 ({later['label']})", f"{later['apparent_temperature']:.1f}℃")
        elif data:
            st.warning(data["message"])
        elif cached:
            st.caption(f"{cached['city']} 날씨를 불러온 상태예요. {city} 날씨는 '현재 날씨 불러오기'를 다시 눌러 주세요. "
                       "그 전에는 기본값(22℃, 비 없음)으로 추천합니다.")
        else:
            st.caption("날씨를 불러오기 전에는 기본값(22℃, 비 없음)으로 추천합니다.")
    else:
        c1, c2, c3 = st.columns(3)
        temperature = c1.slider("기온", -10.0, 40.0, 22.0, 0.5)
        humidity = c2.slider("습도", 10.0, 100.0, 70.0, 1.0)
        apparent = c3.slider("체감온도", -15.0, 45.0, 22.0, 0.5)
        c4, c5, c6 = st.columns(3)
        rain = c4.checkbox("비가 옴")
        precipitation = c5.number_input("강수량(mm)", 0.0, 100.0, 0.0, 0.5)
        wind = c6.slider("풍속(km/h)", 0.0, 60.0, 8.0, 0.5)
        c7, c8 = st.columns(2)
        if c7.checkbox("저녁 늦게까지 밖에 있어요"):
            later_app = c8.slider("저녁 체감온도", -15.0, 45.0, max(-15.0, apparent - 6), 0.5)
            later = {"temperature": later_app, "apparent_temperature": later_app, "label": "저녁"}

    purpose = st.selectbox("외출 목적", PURPOSES)
    cold = float(user["cold_sensitivity"])
    heat = float(user["heat_sensitivity"])

    if st.button("TOP 3 추천 생성", type="primary", width="stretch"):
        out = recommend(
            items, temperature, apparent, humidity, precipitation, rain, wind,
            purpose, cold, heat, 3,
            exclude_keys=app.store.disliked_item_sets(user["id"]),
            seed=f"{user['id']}-{date.today().isoformat()}",  # 같은 날엔 같은 결과, 날마다 동점 코디가 바뀜
            later=later,
        )
        sid = app.store.create_recommendation_session(
            user["id"], purpose, temperature, apparent,
            humidity, precipitation, rain, wind, source
        )
        for rank, result in enumerate(out["results"], 1):
            result["recommendation_id"] = app.store.save_recommendation(sid, rank, result)
        st.session_state.recommendations = out["results"]
        st.session_state.recommendation_session_id = sid
        st.session_state.rec_meta = {"warnings": out["warnings"], "context": out["context"]}

    results = st.session_state.recommendations
    meta = st.session_state.rec_meta or {}
    if meta.get("context"):
        ctx = meta["context"]
        line = f"실제 체감 {ctx['t_env']:.1f}℃ · 추천 기준 **{ctx['t_filter']:.1f}℃**"
        if ctx["purpose"] == "운동":
            line += " (실외 운동 +5℃)"
        if ctx["adjustments"]:
            adj = ", ".join(f"{label} {delta:+.1f}℃" for label, delta in ctx["adjustments"])
            line += f" · 두께는 {adj}를 반영해 {ctx['t_pref']:.1f}℃에 맞춰 골라요"
        st.caption(line)
        if ctx.get("later"):
            lt = ctx["later"]
            st.caption(f"{lt['label']}: 체감 {lt['t_env']:.1f}℃ (지금보다 {ctx['t_env'] - lt['t_env']:.1f}℃ 낮음)")
    for w in meta.get("warnings", []):
        st.warning(w)
    if results == []:
        st.error("추천할 수 있는 코디가 없습니다.")
    if results:
        st.caption("점수 차이가 2점 안이면 색 조합이 더 잘 어울리는 코디를 앞에 둬요.")
        for rank, result in enumerate(results, 1):
            rid = result["recommendation_id"]
            with st.container(border=True):
                st.markdown(f"## {rank}위 · {result['score']*100:.1f}%")
                st.write(" + ".join(p["name"] for p in result["pieces"]))
                if result.get("carry"):
                    st.markdown(f"🧥 **저녁 대비 겉옷:** {result['carry']['name']}")
                for reason in result["reasons"]:
                    st.write(f"- {reason}")
                with st.expander("점수 근거"):
                    st.dataframe(pd.DataFrame(explain_score_breakdown(result["components"], purpose)),
                                 hide_index=True, width="stretch")
                tryon_button(app, result["pieces"], f"rec_{rid}")
                c1, c2, c3 = st.columns(3)
                if c1.button("이 코디 저장", key=f"s_{rid}"):
                    app.store.save_outfit(user["id"], rid)
                    app.store.complete_recommendation_session(st.session_state.recommendation_session_id)
                    st.success("코디를 저장했습니다.")
                if c2.button("👍 좋아요", key=f"l_{rid}"):
                    app.store.upsert_feedback(user["id"], rid, rating="like")
                    st.toast("좋아요를 기록했습니다.")
                if c3.button("👎 별로", key=f"d_{rid}"):
                    app.store.upsert_feedback(user["id"], rid, rating="dislike")
                    st.toast("다음 추천부터 이 조합은 제외합니다.")

                with st.expander("착용 후 피드백"):
                    thermal = st.radio(
                        "체감", list(THERMAL_FEEDBACK), index=0, horizontal=True,
                        format_func=lambda x: THERMAL_FEEDBACK[x], key=f"t_{rid}",
                    )
                    rating = st.slider("코디 만족도", 1, 5, 3, key=f"r_{rid}")
                    worn = st.checkbox("실제로 착용함", key=f"w_{rid}")
                    if st.button("피드백 저장", key=f"f_{rid}"):
                        app.store.upsert_feedback(user["id"], rid, worn=worn, thermal_feedback=thermal, satisfaction=rating)
                        st.success("저장했습니다.")
