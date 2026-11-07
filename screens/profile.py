"""프로필: 사용자 만들기(공개 배포는 내 정보 저장)와 성별·체감 성향 고치기."""
from __future__ import annotations

import sqlite3

import streamlit as st

from screens.common import THERMAL_OPTIONS, THERMAL_TO_SENSITIVITY, thermal_label


def render(app):
    st.title("프로필")
    if not app.cloud or app.current_user() is None:
        with st.form("create_user"):
            st.subheader("내 정보" if app.cloud else "새 사용자")
            name = st.text_input("이름 / 닉네임")
            gender_label = st.radio("성별", ["남성", "여성"], horizontal=True)
            gender = "male" if gender_label == "남성" else "female"
            thermal = st.radio("체감 성향", THERMAL_OPTIONS, index=2, horizontal=True)
            cold, heat = THERMAL_TO_SENSITIVITY[thermal]

            if st.form_submit_button("저장" if app.cloud else "사용자 생성", type="primary"):
                if not name.strip():
                    st.error("이름을 입력하세요.")
                else:
                    try:
                        uid = app.store.create_user(name.strip(), gender, cold, heat)
                    except sqlite3.IntegrityError:
                        st.error(f"'{name.strip()}'은(는) 이미 있는 이름입니다. 다른 이름을 입력하세요.")
                    else:
                        st.session_state.user_id = uid
                        st.success("저장했습니다." if app.cloud else "사용자를 생성했습니다.")
                        st.rerun()

    user = app.current_user()
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
                app.store.update_user(user["id"], gender2, cold2, heat2)
                st.success("저장했습니다.")
                st.rerun()
