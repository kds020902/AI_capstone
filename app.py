"""AI 옷장 (Streamlit). 설정·저장소·사이드바·메뉴만 여기 두고, 각 화면은 screens/ 에 있다.

Streamlit은 버튼을 누를 때마다 이 파일을 처음부터 다시 실행한다. 실행될 때마다 App(저장소·사진 저장소·
그번에만 쓰는 캐시)을 새로 만들어 화면에 넘긴다 (screens/context.py).
"""
from pathlib import Path

import streamlit as st

from core import db
from core.config import UnsafeKeyError, cloud_settings, get_setting
from core.media import LocalMedia
from core.vision import model_status
from screens import home, kpi, profile, recommend, saved, shopping, wardrobe
from screens.common import APP_NAME, gender_ko
from screens.context import App, clear_recommendations
from screens.login import cloud_login_gate

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "data" / "uploads"

st.set_page_config(page_title=APP_NAME, page_icon="👕", layout="wide")

for key, value in {
    "user_id": None,
    "active_user_id": None,
    "recommendations": None,
    "recommendation_session_id": None,
    "rec_meta": None,
    "weather_cache": None,
    "ba_cards": [],      # 직접 추가: 확인 중인 사진 카드 (최대 10장)
    "ba_idx": 0,
    "ba_seq": 0,
    "ba_nonce": 0,
    "ba_more": False,
    "ba_flash": [],
    "ba_names": set(),
}.items():
    st.session_state.setdefault(key, value)

# ------------------------------------------------------------------ 저장소: 로컬(SQLite) 또는 공개 배포(Supabase)
try:
    CLOUD = cloud_settings()
except UnsafeKeyError as e:
    st.error(str(e))
    st.stop()

if CLOUD:
    store, media = cloud_login_gate(CLOUD)
else:
    store, media = db, LocalMedia(UPLOAD_DIR)
    store.init_db()
app = App(store, media, cloud=CLOUD, tryon_url=get_setting("TRYON_SPACE_URL"), web_url=get_setting("WEB_TRIAL_URL"))

# ------------------------------------------------------------------ 사이드바
st.sidebar.title(f"👕 {APP_NAME}")
if CLOUD:
    st.sidebar.caption(f"로그인: {st.session_state.get('auth_email') or '-'}")
    if st.sidebar.button("로그아웃"):
        from core import cloud_auth
        cloud_auth.sign_out(st.session_state.sb_client)
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()
    if not app.current_user():
        st.sidebar.info("프로필에서 내 정보를 먼저 저장하세요.")
else:
    users = store.list_users()
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
    clear_recommendations()
    st.session_state.ba_cards = []
    st.session_state.ba_idx = 0
    st.session_state.ba_more = False
    st.session_state.ba_flash = []

PAGES = {"홈": home, "프로필": profile, "내 옷장": wardrobe, "오늘의 추천": recommend,
         "사기 전에 맞춰보기": shopping, "저장 코디": saved, "KPI·데이터": kpi}
page = st.sidebar.radio("메뉴", list(PAGES))
st.sidebar.divider()
st.sidebar.caption(f"의류 분류: {model_status()['classifier']}")
if app.web_url:
    st.sidebar.link_button("설치 없는 웹 체험판", app.web_url)

PAGES[page].render(app)
