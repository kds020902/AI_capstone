from datetime import date
from pathlib import Path
import sqlite3
import time

import pandas as pd
import streamlit as st
from PIL import Image

from core import db
from core.care import get_care_guide
from core.recommender import recommend, explain_score_breakdown
from core.taxonomy import MAIN_CATEGORIES, NUM_SUBCATEGORIES, RULES, SUBCATEGORIES, main_category_for_subcategory
from core.vision import classify_clothing_image, dominant_color_hex, dominant_color_name, model_status
from core.weather import CITY_PRESETS, get_current_weather

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

page = st.sidebar.radio("메뉴", ["홈", "프로필", "내 옷장", "오늘의 추천", "저장 코디", "KPI·데이터"])
st.sidebar.divider()
status = model_status()
st.sidebar.caption(f"의류 분류: {status['classifier']}")
st.sidebar.caption(f"추천 랭커: {status['ranker']}")

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
        c1, c2 = st.columns(2)
        category_filter = c1.selectbox("카테고리", ["전체"] + MAIN_CATEGORIES)
        search = c2.text_input("이름/세부분류 검색")
        filtered = items
        if category_filter != "전체":
            filtered = [x for x in filtered if x["category"] == category_filter]
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
                            f"두께 {item['warmth']}/5"
                        )
                        st.write(f"색상: {item['color'] or '-'} · 비 적합: {'O' if item['rain_ok'] else 'X'}")
                        if item.get("material"):
                            st.caption(f"세탁: {get_care_guide(item['material'])['text']}")
                        c_a, c_b = st.columns(2)
                        if c_a.button("비활성화", key=f"off_{item['id']}"):
                            db.deactivate_item(item["id"], user["id"])
                            st.rerun()
                        if c_b.button("삭제", key=f"del_{item['id']}"):
                            db.soft_delete_item(item["id"], user["id"])
                            st.rerun()

        # 비활성화한 옷 다시 활성화
        inactive = db.list_inactive_items(user["id"])
        with st.expander(f"비활성화한 옷 ({len(inactive)}개)"):
            if not inactive:
                st.caption("비활성화한 옷이 없습니다.")
            for item in inactive:
                c_a, c_b = st.columns([4, 1])
                c_a.write(f"{item['name']} · {item['category']} / {item.get('subcategory') or '-'}")
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

    if mode == "자동 조회":
        city = st.selectbox("도시", list(CITY_PRESETS))
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
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("기온", f"{temperature:.1f}℃")
            c2.metric("체감", f"{apparent:.1f}℃")
            c3.metric("습도", f"{humidity:.0f}%")
            c4.metric("풍속", f"{wind:.1f} km/h")
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

    purpose = st.selectbox("외출 목적", ["등교", "데이트", "운동", "격식"])
    cold = float(user["cold_sensitivity"])
    heat = float(user["heat_sensitivity"])

    if st.button("TOP 3 추천 생성", type="primary", use_container_width=True):
        out = recommend(
            items, temperature, apparent, humidity, precipitation, rain, wind,
            purpose, cold, heat, 3,
            exclude_keys=db.disliked_item_sets(user["id"]),
            seed=f"{user['id']}-{date.today().isoformat()}",  # 같은 날엔 같은 결과, 날마다 동점 코디가 바뀜
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

    train_df = db.build_ranker_training_dataframe(user["id"])
    if not train_df.empty:
        st.subheader("랭커 학습 데이터")
        st.dataframe(train_df, use_container_width=True, hide_index=True)
        st.download_button("랭커 학습 CSV", train_df.to_csv(index=False).encode("utf-8-sig"),
                           f"ranker_user_{user['id']}.csv", "text/csv")
