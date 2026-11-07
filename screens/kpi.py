"""KPI·데이터: 추천 세션 수, TOP 3 수용률, 추천 로그 CSV."""
from __future__ import annotations

import streamlit as st


def render(app):
    st.title("KPI · 데이터")
    user = app.need_user()
    stats = app.store.user_kpi_stats(user["id"])
    c1, c2 = st.columns(2)
    c1.metric("추천 세션", stats["completed_sessions"])
    c2.metric("Top 3 수용률", "-" if stats["top3_acceptance_rate"] is None else f"{stats['top3_acceptance_rate']*100:.1f}%")

    logs = app.store.export_user_logs(user["id"])
    if not logs.empty:
        st.dataframe(logs, width="stretch", hide_index=True)
        st.download_button("추천 로그 CSV", logs.to_csv(index=False).encode("utf-8-sig"),
                           f"user_{user['id']}_logs.csv", "text/csv")
