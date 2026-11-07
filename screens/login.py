"""공개 배포 모드(Supabase)의 로그인·회원가입 화면."""
from __future__ import annotations

import streamlit as st

from core.media import PhotoCache
from screens.common import APP_NAME


def cloud_login_gate(settings):
    """로그인 전이면 로그인/가입 화면을 그리고 멈춘다. 로그인했으면 (store, media)."""
    from core import cloud_auth
    from core.cloud_store import CloudStore
    from core.media import SupabaseMedia

    if "sb_client" not in st.session_state:
        st.session_state.sb_client = cloud_auth.make_client(settings)
    client = st.session_state.sb_client
    user = cloud_auth.current_user(client)
    if user is None:
        st.title(f"👕 {APP_NAME}")
        st.caption("날씨·외출 목적·체질에 맞춰 내 옷장 속 옷으로 코디를 추천해요. 내 옷장은 로그인한 나만 볼 수 있어요.")
        tab_in, tab_up = st.tabs(["로그인", "회원가입"])
        with tab_in, st.form("login"):
            email = st.text_input("이메일", key="login_email")
            pw = st.text_input("비밀번호", type="password", key="login_pw")
            if st.form_submit_button("로그인", type="primary"):
                ok, msg = cloud_auth.sign_in(client, email.strip(), pw)
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()
        with tab_up, st.form("signup"):
            email2 = st.text_input("이메일", key="signup_email")
            pw2 = st.text_input("비밀번호 (6자 이상)", type="password", key="signup_pw")
            pw3 = st.text_input("비밀번호 확인", type="password", key="signup_pw2")
            if st.form_submit_button("가입하기", type="primary"):
                if pw2 != pw3:
                    st.error("비밀번호 확인이 달라요.")
                else:
                    ok, msg = cloud_auth.sign_up(client, email2.strip(), pw2)
                    (st.success if ok else st.info)(msg)
                    if ok:
                        st.rerun()
        st.caption("무료 체험 서비스예요 (비상업·학습·시연용). 사진과 기록은 내 계정에만 저장돼요.")
        st.stop()
    st.session_state.auth_email = user.email
    st.session_state.user_id = str(user.id)
    photos = st.session_state.setdefault("photo_cache", PhotoCache())   # 다시 그릴 때마다 사진을 내려받지 않게
    return CloudStore(client, user.id), SupabaseMedia(client, user.id, cache=photos)
