"""저장 코디 목록."""
from __future__ import annotations

import streamlit as st


def render(app):
    st.title("저장 코디")
    user = app.need_user()
    saved = app.store.list_saved_outfits(user["id"])
    if not saved:
        st.info("저장된 코디가 없습니다.")
    for row in saved:
        with st.container(border=True):
            st.markdown(f"### {row['saved_at'][:16]} · 추천 {row['rank']}위")
            st.write(row["outfit_text"])
            st.caption(f"{row['purpose']} · {row['temperature']:.1f}℃ · 습도 {row['humidity']:.0f}% · 점수 {row['score']*100:.1f}%")
