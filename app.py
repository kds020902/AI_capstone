from datetime import date
from pathlib import Path
import sqlite3
import time

import pandas as pd
import streamlit as st
from PIL import Image

from core import db
from core.care import get_care_guide
from core.colors import COLOR_KO, normalize_color
from core.recommender import recommend, explain_score_breakdown
from core.shopping import candidate_report
from core.taxonomy import (
    MAIN_CATEGORIES, NUM_SUBCATEGORIES, PURPOSES, RULES, SEASONS, SUBCATEGORIES, current_season, item_seasons,
    main_category_for_subcategory, manual_seasons, season_label, seasons_for,
)
from core.vision import classify_clothing_image, dominant_color_hex, dominant_color_name, model_status
from core.weather import CITY_PRESETS, RETURN_HOURS, coldest_until, get_current_weather

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

APP_NAME = "AI 옷장"
st.set_page_config(page_title=APP_NAME, page_icon="👕", layout="wide")
db.init_db()

for key, value in {
    "user_id": None,
    "active_user_id": None,
    "recommendations": None,
    "recommendation_session_id": None,
    "rec_meta": None,
    "weather_cache": None,
    "last_ai_result": None,
}.items():
    st.session_state.setdefault(key, value)


def current_user():
    if not st.session_state.user_id:
        return None
    return db.get_user(st.session_state.user_id)


def gender_ko(g):
    return "여성" if g == "female" else "남성"


THERMAL_OPTIONS = ["추위를 잘 탐", "추위를 조금 탐", "보통", "더위를 조금 탐", "더위를 잘 탐"]
THERMAL_TO_SENSITIVITY = {
    "추위를 잘 탐": (1.0, 0.0),
    "추위를 조금 탐": (0.6, 0.1),
    "보통": (0.0, 0.0),
    "더위를 조금 탐": (0.1, 0.6),
    "더위를 잘 탐": (0.0, 1.0),
}
THERMAL_FEEDBACK = {None: "응답 안 함", -2: "매우 추움", -1: "약간 추움", 0: "적당함", 1: "약간 더움", 2: "매우 더움"}


@st.cache_data(show_spinner=False, max_entries=64)
def cached_candidate_report(cand, items, cold, heat):
    return candidate_report(cand, items, cold, heat)


def score_cell(cell, improved):
    if cell["best"] is None:
        return "–"
    return f"{cell['best'] * 100:.0f}점" + (" ▲" if improved else "")


def thermal_label(cold, heat):
    cold, heat = float(cold), float(heat)
    if cold >= 0.8:
        return "추위를 잘 탐"
    if cold >= 0.45:
        return "추위를 조금 탐"
    if heat >= 0.8:
        return "더위를 잘 탐"
    if heat >= 0.45:
        return "더위를 조금 탐"
    return "보통"


# ------------------------------------------------------------------ 사이드바
st.sidebar.title(f"👕 {APP_NAME}")
users = db.list_users()
if users:
    options = {f"{u['name']} · {gender_ko(u['gender'])} (ID {u['id']})": u["id"] for u in users}
    labels = list(options)
    idx = next((i for i, lb in enumerate(labels) if options[lb] == st.session_state.user_id), 0)
    selected = st.sidebar.selectbox("사용자", labels, index=idx)
    st.session_state.user_id = options[selected]
else:
    st.sidebar.info("프로필에서 사용자를 먼저 생성하세요.")

# 사용자를 바꾸면 이전 사용자의 추천·AI 분류 결과를 비운다 (안 비우면 다른 사람의 추천이 저장될 수 있음)
if st.session_state.active_user_id != st.session_state.user_id:
    st.session_state.active_user_id = st.session_state.user_id
    st.session_state.recommendations = None
    st.session_state.recommendation_session_id = None
    st.session_state.rec_meta = None
    st.session_state.last_ai_result = None

page = st.sidebar.radio("메뉴", ["홈", "프로필", "내 옷장", "오늘의 추천", "사기 전에 맞춰보기", "저장 코디", "KPI·데이터"])
st.sidebar.divider()
status = model_status()
st.sidebar.caption(f"의류 분류: {status['classifier']}")

# ------------------------------------------------------------------ 홈
if page == "홈":
    st.title(APP_NAME)
    st.subheader(f"성별 프로필 + 30개 캐주얼 스타터 옷장 + {NUM_SUBCATEGORIES}종 세부분류")
    user = current_user()
    if user:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("프로필", f"{user['name']} · {gender_ko(user['gender'])}")
        c2.metric("등록 의류", db.count_wardrobe_items(user["id"]))
        c3.metric("추천 세션", db.count_recommendation_sessions(user["id"]))
        acc = db.top3_acceptance_rate(user["id"])
        c4.metric("Top 3 수용", "-" if acc is None else f"{acc*100:.1f}%")
        st.info(
            "스타터 옷장은 실제 소유 여부를 대신 확정하는 기능이 아닙니다. "
            "처음 테스트할 때 30개를 한 번에 불러오고, 없는 옷은 비활성화/삭제해서 사용하세요."
        )
    else:
        st.warning("프로필을 생성하세요.")

# ------------------------------------------------------------------ 프로필
elif page == "프로필":
    st.title("프로필")
    with st.form("create_user"):
        st.subheader("새 사용자")
        name = st.text_input("이름 / 닉네임")
        gender_label = st.radio("성별", ["남성", "여성"], horizontal=True)
        gender = "male" if gender_label == "남성" else "female"
        thermal = st.radio("체감 성향", THERMAL_OPTIONS, index=2, horizontal=True)
        cold, heat = THERMAL_TO_SENSITIVITY[thermal]

        if st.form_submit_button("사용자 생성", type="primary"):
            if not name.strip():
                st.error("이름을 입력하세요.")
            else:
                try:
                    uid = db.create_user(name.strip(), gender, cold, heat)
                except sqlite3.IntegrityError:
                    st.error(f"'{name.strip()}'은(는) 이미 있는 이름입니다. 다른 이름을 입력하세요.")
                else:
                    st.session_state.user_id = uid
                    st.success("사용자를 생성했습니다.")
                    st.rerun()

    user = current_user()
    if user:
        st.divider()
        st.subheader("현재 사용자 설정")
        with st.form("edit_user"):
            gender_label2 = st.radio("성별", ["남성", "여성"], index=0 if user["gender"] == "male" else 1, horizontal=True)
            gender2 = "male" if gender_label2 == "남성" else "female"
            current_thermal = thermal_label(user["cold_sensitivity"], user["heat_sensitivity"])
            thermal2 = st.radio("체감 성향", THERMAL_OPTIONS, index=THERMAL_OPTIONS.index(current_thermal), horizontal=True)
            cold2, heat2 = THERMAL_TO_SENSITIVITY[thermal2]
            if st.form_submit_button("설정 저장"):
                db.update_user(user["id"], gender2, cold2, heat2)
                st.success("저장했습니다.")
                st.rerun()

# ------------------------------------------------------------------ 내 옷장
elif page == "내 옷장":
    st.title("내 옷장")
    user = current_user()
    if not user:
        st.warning("사용자를 생성하세요.")
        st.stop()

    st.caption(f"현재 프로필: {user['name']} · {gender_ko(user['gender'])}")
    tab1, tab2, tab3 = st.tabs(["옷장 목록", "30개 스타터 옷장", "사진으로 직접 추가"])

    with tab1:
        items = db.list_wardrobe_items(user["id"], active_only=True)
        season_now = current_season(date.today().month)
        with st.expander(f"계절 정리 · 지금은 {season_now}"):
            season = st.radio("계절", SEASONS, index=SEASONS.index(season_now), horizontal=True, key="tidy_season")
            off = [x for x in items if season not in item_seasons(x)]
            back = [x for x in db.list_inactive_items(user["id"]) if season in item_seasons(x)]
            c1, c2 = st.columns(2)
            if c1.button(f"{season}에 안 입는 옷 {len(off)}벌 넣어두기", disabled=not off):
                db.set_items_active([x["id"] for x in off], user["id"], False)
                st.rerun()
            if c2.button(f"넣어 둔 옷 중 {season} 옷 {len(back)}벌 꺼내기", disabled=not back):
                db.set_items_active([x["id"] for x in back], user["id"], True)
                st.rerun()
            st.caption("넣어 둔 옷은 추천에 쓰지 않아요. 아래 '넣어 둔 옷'에서 한 벌씩 꺼낼 수도 있어요.")

        c1, c2, c3 = st.columns(3)
        category_filter = c1.selectbox("카테고리", ["전체"] + MAIN_CATEGORIES)
        season_filter = c2.selectbox("계절", ["전체"] + SEASONS)
        search = c3.text_input("이름/세부분류 검색")
        filtered = items
        if category_filter != "전체":
            filtered = [x for x in filtered if x["category"] == category_filter]
        if season_filter != "전체":
            filtered = [x for x in filtered if season_filter in item_seasons(x)]
        if search.strip():
            q = search.lower()
            filtered = [x for x in filtered if q in x["name"].lower() or q in str(x.get("subcategory") or "").lower()]

        if not filtered:
            st.info("등록된 의류가 없습니다.")
        else:
            cols = st.columns(3)
            for i, item in enumerate(filtered):
                with cols[i % 3]:
                    with st.container(border=True):
                        if item["image_path"] and Path(item["image_path"]).exists():
                            st.image(item["image_path"])
                        st.markdown(f"### {item['name']}")
                        st.caption(
                            f"{item['category']} / {item.get('subcategory') or '세부분류 없음'} · "
                            f"두께 {item['warmth']}/5 · {season_label(item_seasons(item))}"
                            + (" (직접 지정)" if manual_seasons(item) else "")
                        )
                        st.write(f"색상: {item['color'] or '-'} · 비 적합: {'O' if item['rain_ok'] else 'X'}")
                        if item.get("material"):
                            st.caption(f"세탁: {get_care_guide(item['material'])['text']}")
                        with st.popover("계절 수정"):
                            picked = st.multiselect("입는 계절", SEASONS, default=item_seasons(item),
                                                    key=f"season_{item['id']}")
                            if st.button("저장", key=f"season_save_{item['id']}"):
                                same_as_auto = picked == seasons_for(item.get("subcategory"))
                                db.update_item_seasons(item["id"], user["id"], None if same_as_auto else picked)
                                st.rerun()
                            st.caption(f"자동: {season_label(seasons_for(item.get('subcategory')))} (옷 종류 기준). "
                                       "전부 지우고 저장하면 자동으로 돌아가요.")
                        c_a, c_b = st.columns(2)
                        if c_a.button("비활성화", key=f"off_{item['id']}"):
                            db.deactivate_item(item["id"], user["id"])
                            st.rerun()
                        if c_b.button("삭제", key=f"del_{item['id']}"):
                            db.soft_delete_item(item["id"], user["id"])
                            st.rerun()

        # 비활성화한 옷 다시 활성화
        inactive = db.list_inactive_items(user["id"])
        with st.expander(f"넣어 둔 옷 · 비활성화 ({len(inactive)}개)"):
            if not inactive:
                st.caption("넣어 둔 옷이 없습니다.")
            for item in inactive:
                c_a, c_b = st.columns([4, 1])
                c_a.write(f"{item['name']} · {item['category']} / {item.get('subcategory') or '-'} · "
                          f"{season_label(item_seasons(item))}")
                if c_b.button("다시 활성화", key=f"on_{item['id']}"):
                    db.reactivate_item(item["id"], user["id"])
                    st.rerun()

    with tab2:
        st.subheader(f"{gender_ko(user['gender'])} 캐주얼 스타터 옷장 30개")
        st.write(
            "일일이 옷을 등록하지 않고 테스트를 시작할 수 있도록 자주 쓰이는 캐주얼 아이템 30개를 넣습니다. "
            "실제로 보유하지 않은 항목은 목록에서 비활성화/삭제하세요."
        )
        if st.button("프로필 성별 기준 30개 추가", type="primary"):
            added = db.add_starter_wardrobe(user["id"])
            st.success(f"{added}개를 추가했습니다. 이미 있던 이름은 중복 추가하지 않았습니다.")
            st.rerun()

    with tab3:
        st.subheader("사진 업로드")
        uploaded = st.file_uploader("JPG/PNG", type=["jpg", "jpeg", "png"])
        color_guess = ""
        if uploaded:
            image = Image.open(uploaded).convert("RGB")
            st.image(image, width=300)
            color_guess = dominant_color_name(image) or ""
            st.write(f"대표색 추정: `{color_guess}` ({dominant_color_hex(image)})")
            if st.button("AI 분류 시도"):
                st.session_state.last_ai_result = classify_clothing_image(image)
            ai = st.session_state.last_ai_result
            if ai:
                if ai.get("ok"):
                    st.success(
                        f"AI 후보: {ai.get('subcategory') or '-'} → {ai.get('category') or '-'} "
                        f"({ai['confidence']*100:.1f}%)"
                    )
                else:
                    st.warning(ai["message"])

        default_sub = None
        if st.session_state.last_ai_result and st.session_state.last_ai_result.get("ok"):
            default_sub = st.session_state.last_ai_result.get("subcategory")
        sub_index = SUBCATEGORIES.index(default_sub) if default_sub in SUBCATEGORIES else 0

        # 세부분류·대분류는 폼 밖에 둔다: 폼 안 위젯은 제출 전까지 다시 그려지지 않아
        # 세부분류를 바꿔도 대분류가 따라오지 않는다 (예: '상의 / 청바지'로 저장)
        subcategory = st.selectbox(f"세부분류({NUM_SUBCATEGORIES}종)", SUBCATEGORIES, index=sub_index)
        rule = RULES[subcategory]
        predicted_main = main_category_for_subcategory(subcategory)
        category = st.selectbox(
            "최종 대분류", MAIN_CATEGORIES, index=MAIN_CATEGORIES.index(predicted_main), key=f"cat_{subcategory}"
        )

        with st.form("add_item"):
            name = st.text_input("의류명")
            color = st.text_input("색상 (예: black, 네이비, #1f2a44)", value=color_guess)
            warmth = st.slider("두께/보온", 1, 5, rule["default_warmth"], key=f"warmth_{subcategory}")
            rain_ok = st.checkbox("비 오는 날 부담이 적음", value=rule["default_rain_ok"], key=f"rain_{subcategory}")
            material = st.selectbox("소재", ["", "cotton", "polyester", "denim", "wool", "linen", "nylon", "other"])
            if st.form_submit_button("저장", type="primary"):
                if not name.strip():
                    st.error("의류명을 입력하세요.")
                elif uploaded is None:
                    st.error("사진을 업로드하세요.")
                else:
                    suffix = Path(uploaded.name).suffix.lower() or ".jpg"
                    path = UPLOAD_DIR / f"{user['id']}_{int(time.time()*1000)}{suffix}"
                    path.write_bytes(uploaded.getvalue())
                    ai = st.session_state.last_ai_result or {}
                    db.create_wardrobe_item(
                        user["id"], name.strip(), category, subcategory, color.strip(),
                        warmth, rain_ok, material, str(path), "",
                        ai.get("category") if ai.get("ok") else None,
                        ai.get("subcategory") if ai.get("ok") else None,
                        ai.get("confidence") if ai.get("ok") else None,
                    )
                    st.session_state.last_ai_result = None
                    st.success("등록했습니다.")
                    st.rerun()

# ------------------------------------------------------------------ 오늘의 추천
elif page == "오늘의 추천":
    st.title("오늘의 추천")
    user = current_user()
    if not user:
        st.warning("사용자를 생성하세요.")
        st.stop()
    items = db.list_wardrobe_items(user["id"], active_only=True)
    cats = {x["category"] for x in items}
    if not (("상의" in cats and "하의" in cats and "신발" in cats) or ("원피스" in cats and "신발" in cats)):
        st.warning("상의+하의+신발 또는 원피스+신발 조합이 필요합니다.")
        st.stop()

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
            st.session_state.weather_cache = get_current_weather(lat, lon)
        data = st.session_state.weather_cache
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

    purpose = st.selectbox("외출 목적", ["등교", "데이트", "운동", "격식"])
    cold = float(user["cold_sensitivity"])
    heat = float(user["heat_sensitivity"])

    if st.button("TOP 3 추천 생성", type="primary", use_container_width=True):
        out = recommend(
            items, temperature, apparent, humidity, precipitation, rain, wind,
            purpose, cold, heat, 3,
            exclude_keys=db.disliked_item_sets(user["id"]),
            seed=f"{user['id']}-{date.today().isoformat()}",  # 같은 날엔 같은 결과, 날마다 동점 코디가 바뀜
            later=later,
        )
        sid = db.create_recommendation_session(
            user["id"], purpose, temperature, apparent,
            humidity, precipitation, rain, wind, source
        )
        for rank, result in enumerate(out["results"], 1):
            result["recommendation_id"] = db.save_recommendation(sid, rank, result)
        st.session_state.recommendations = out["results"]
        st.session_state.recommendation_session_id = sid
        st.session_state.rec_meta = {"warnings": out["warnings"], "context": out["context"]}

    results = st.session_state.recommendations
    meta = st.session_state.rec_meta or {}
    if meta.get("context"):
        ctx = meta["context"]
        adj = ", ".join(f"{label} {delta:+.0f}℃" for label, delta in ctx["adjustments"]) or "보정 없음"
        st.caption(f"유효 기온: 체감 {ctx['base_temp']:.1f}℃ → ({adj}) → **{ctx['eff']:.1f}℃** 기준으로 추천")
        if ctx.get("later"):
            lt = ctx["later"]
            st.caption(f"{lt['label']}: 체감 {lt['base_temp']:.1f}℃ → 유효 **{lt['eff']:.1f}℃** "
                       f"(지금보다 {ctx['eff'] - lt['eff']:.1f}℃ 낮음)")
    for w in meta.get("warnings", []):
        st.warning(w)
    if results == []:
        st.error("추천할 수 있는 코디가 없습니다.")
    if results:
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
                                 hide_index=True, use_container_width=True)
                c1, c2, c3 = st.columns(3)
                if c1.button("이 코디 저장", key=f"s_{rid}"):
                    db.save_outfit(user["id"], rid)
                    db.complete_recommendation_session(st.session_state.recommendation_session_id)
                    st.success("코디를 저장했습니다.")
                if c2.button("👍 좋아요", key=f"l_{rid}"):
                    db.upsert_feedback(user["id"], rid, rating="like")
                    st.toast("좋아요를 기록했습니다.")
                if c3.button("👎 별로", key=f"d_{rid}"):
                    db.upsert_feedback(user["id"], rid, rating="dislike")
                    st.toast("다음 추천부터 이 조합은 제외합니다.")

                with st.expander("착용 후 피드백"):
                    thermal = st.radio(
                        "체감", list(THERMAL_FEEDBACK), index=0, horizontal=True,
                        format_func=lambda x: THERMAL_FEEDBACK[x], key=f"t_{rid}",
                    )
                    rating = st.slider("코디 만족도", 1, 5, 3, key=f"r_{rid}")
                    worn = st.checkbox("실제로 착용함", key=f"w_{rid}")
                    if st.button("피드백 저장", key=f"f_{rid}"):
                        db.upsert_feedback(user["id"], rid, worn=worn, thermal_feedback=thermal, satisfaction=rating)
                        st.success("저장했습니다.")

# ------------------------------------------------------------------ 사기 전에 맞춰보기
elif page == "사기 전에 맞춰보기":
    st.title("사기 전에 맞춰보기")
    st.caption("쇼핑몰 상품 사진이나 화면 캡처를 올리면 지금 옷장과 맞춰 봐요. 옷장에는 넣지 않고 '구매 후보'로 따로 둬요.")
    user = current_user()
    if not user:
        st.stop()
    items = db.list_wardrobe_items(user["id"], active_only=True)
    candidates = db.list_candidates(user["id"])

    with st.expander("구매 후보 추가", expanded=not candidates):
        up = st.file_uploader("상품 사진 또는 캡처 (JPG/PNG)", type=["jpg", "jpeg", "png"], key="cand_upload")
        cand_ai, cand_color = None, ""
        if up:
            cand_image = Image.open(up).convert("RGB")
            st.image(cand_image, width=220)
            cand_color = dominant_color_name(cand_image) or ""
            ai_key = f"cand_ai_{up.file_id}"  # 다시 그릴 때마다 분류하지 않도록
            if ai_key not in st.session_state:
                st.session_state[ai_key] = classify_clothing_image(cand_image)
            cand_ai = st.session_state[ai_key]
            if cand_ai.get("ok"):
                st.caption(f"AI: {cand_ai['subcategory']} ({cand_ai['confidence'] * 100:.0f}%) · 색 {COLOR_KO.get(cand_color, cand_color)}")
            else:
                st.caption(cand_ai["message"])
        ai_sub = cand_ai.get("subcategory") if cand_ai and cand_ai.get("ok") else None
        c1, c2 = st.columns(2)
        cand_sub = c1.selectbox("종류", SUBCATEGORIES,
                                index=SUBCATEGORIES.index(ai_sub) if ai_sub in SUBCATEGORIES else 0, key=f"cand_sub_{ai_sub}")
        colors = list(COLOR_KO)
        cand_color = c2.selectbox("색", colors, index=colors.index(cand_color) if cand_color in colors else 0,
                                  format_func=lambda c: COLOR_KO[c], key=f"cand_color_{cand_color}")
        rule = RULES[cand_sub]
        c3, c4 = st.columns(2)
        cand_warmth = c3.slider("두께/보온", 1, 5, rule["default_warmth"], key=f"cand_w_{cand_sub}")
        cand_rain = c4.checkbox("비 오는 날 부담이 적음", value=rule["default_rain_ok"], key=f"cand_r_{cand_sub}")
        cand_name = st.text_input("이름", value=f"{COLOR_KO[cand_color]} {cand_sub}", key=f"cand_name_{cand_color}_{cand_sub}")
        cand_link = st.text_input("상품 링크 (선택, 메모용)", key="cand_link")
        if st.button("구매 후보로 저장", type="primary", disabled=not cand_name.strip()):
            path = ""
            if up:
                path = str(UPLOAD_DIR / f"cand_{user['id']}_{int(time.time() * 1000)}{Path(up.name).suffix.lower()}")
                Image.open(up).convert("RGB").save(path)
            db.create_candidate(user["id"], cand_name.strip(), main_category_for_subcategory(cand_sub), cand_sub,
                                cand_color, cand_warmth, cand_rain, image_path=path, link=cand_link.strip(),
                                ai_subcategory=ai_sub, ai_confidence=cand_ai.get("confidence") if cand_ai else None)
            st.rerun()

    if not candidates:
        st.info("아직 구매 후보가 없어요. 위에서 상품 사진을 올려 보세요.")
        st.stop()
    season_now = current_season(date.today().month)
    c1, c2 = st.columns(2)
    view_season = c1.radio("코디를 볼 계절", SEASONS, index=SEASONS.index(season_now), horizontal=True)
    view_purpose = c2.radio("외출 목적", PURPOSES, horizontal=True)
    cold, heat = float(user["cold_sensitivity"]), float(user["heat_sensitivity"])
    owned = {x["id"]: x["name"] for x in items}
    VERDICT_BOX = {"dup": st.warning, "none": st.error, "gap": st.success, "fit": st.info}

    for cand in candidates:
        rep = cached_candidate_report(cand, items, cold, heat)
        with st.container(border=True):
            c_img, c_body = st.columns([1, 3])
            if cand["image_path"] and Path(cand["image_path"]).exists():
                c_img.image(cand["image_path"])
            with c_body:
                st.markdown(f"### {cand['name']}")
                st.caption(f"{cand['category']} / {cand['subcategory']} · {COLOR_KO.get(normalize_color(cand['color']), cand['color'])} · "
                           f"두께 {cand['warmth']}/5 · {season_label(rep['seasons'])}"
                           + (f" · [상품 링크]({cand['link']})" if cand["link"] else ""))
                VERDICT_BOX[rep["verdict"][0]](rep["verdict"][1])
                if rep["similar"]["same_type"]:
                    st.caption("같은 종류로 가진 옷: " + ", ".join(x["name"] for x in rep["similar"]["same_type"]))
                if rep["partners"]:
                    st.caption(f"어울리는 내 옷 {len(rep['partners'])}벌: "
                               + ", ".join(owned[i] for i in rep["partners"][:8] if i in owned)
                               + (" …" if len(rep["partners"]) > 8 else ""))
            gain = {(x["season"], x["purpose"]) for x in rep["improves"]}
            table = pd.DataFrame([[score_cell(rep["cells"][(s, p)], (s, p) in gain) for p in PURPOSES] for s in SEASONS],
                                 index=SEASONS, columns=PURPOSES)
            st.caption("상황별 최고 코디 점수 (– 는 이 옷을 입기 어려운 상황, ▲ 는 이 옷이 있으면 점수가 오르는 상황)")
            st.dataframe(table, use_container_width=True)
            cell = rep["cells"][(view_season, view_purpose)]
            if cell["results"]:
                st.markdown(f"**{view_season} · {view_purpose} 코디 (체감 {cell['temp']}℃ 기준)**")
                for r in cell["results"]:
                    st.write(f"- {r['score'] * 100:.0f}점 · " + " + ".join(p["name"] for p in r["pieces"]))
            else:
                st.caption(f"{view_season} · {view_purpose}에는 이 옷으로 만들 코디가 없어요.")
            c_a, c_b = st.columns(2)
            if c_a.button("샀어요 · 옷장에 넣기", key=f"buy_{cand['id']}"):
                db.buy_candidate(cand["id"], user["id"])
                st.toast(f"{cand['name']}을(를) 옷장에 넣었어요.")
                st.rerun()
            if c_b.button("후보에서 지우기", key=f"cdel_{cand['id']}"):
                db.delete_candidate(cand["id"], user["id"])
                st.rerun()

# ------------------------------------------------------------------ 저장 코디
elif page == "저장 코디":
    st.title("저장 코디")
    user = current_user()
    if not user:
        st.stop()
    saved = db.list_saved_outfits(user["id"])
    if not saved:
        st.info("저장된 코디가 없습니다.")
    for row in saved:
        with st.container(border=True):
            st.markdown(f"### {row['saved_at'][:16]} · 추천 {row['rank']}위")
            st.write(row["outfit_text"])
            st.caption(f"{row['purpose']} · {row['temperature']:.1f}℃ · 습도 {row['humidity']:.0f}% · 점수 {row['score']*100:.1f}%")

# ------------------------------------------------------------------ KPI
elif page == "KPI·데이터":
    st.title("KPI · 데이터")
    user = current_user()
    if not user:
        st.stop()
    stats = db.user_kpi_stats(user["id"])
    c1, c2 = st.columns(2)
    c1.metric("추천 세션", stats["completed_sessions"])
    c2.metric("Top 3 수용률", "-" if stats["top3_acceptance_rate"] is None else f"{stats['top3_acceptance_rate']*100:.1f}%")

    logs = db.export_user_logs(user["id"])
    if not logs.empty:
        st.dataframe(logs, use_container_width=True, hide_index=True)
        st.download_button("추천 로그 CSV", logs.to_csv(index=False).encode("utf-8-sig"),
                           f"user_{user['id']}_logs.csv", "text/csv")
