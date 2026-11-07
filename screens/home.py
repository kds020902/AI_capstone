"""홈: 프로필 요약과 안내."""
from __future__ import annotations

import streamlit as st

from core.taxonomy import NUM_SUBCATEGORIES
from screens.common import APP_NAME, gender_ko


def render(app):
    st.title(APP_NAME)
    st.subheader(f"성별 프로필 + 30개 캐주얼 스타터 옷장 + {NUM_SUBCATEGORIES}종 세부분류")
    user = app.current_user()
    if user:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("프로필", f"{user['name']} · {gender_ko(user['gender'])}")
        c2.metric("등록 의류", app.store.count_wardrobe_items(user["id"]))
        c3.metric("추천 세션", app.store.count_recommendation_sessions(user["id"]))
        acc = app.store.top3_acceptance_rate(user["id"])
        c4.metric("Top 3 수용", "-" if acc is None else f"{acc*100:.1f}%")
        st.info(
            "스타터 옷장은 실제 소유 여부를 대신 확정하는 기능이 아닙니다. "
            "처음 테스트할 때 30개를 한 번에 불러오고, 없는 옷은 비활성화/삭제해서 사용하세요."
        )
    else:
        st.warning("프로필을 생성하세요.")
    if app.cloud:
        with st.expander("무료 체험 서비스 안내"):
            st.markdown(
                "- 비상업·학습·시연용 캡스톤 프로젝트예요. 광고·결제가 없어요.\n"
                "- 무료 서버라 한동안 아무도 안 쓰면 잠들어요. 처음 접속이 느리면 깨우는 중이에요.\n"
                "- 날씨는 Open-Meteo 무료 API를 써요. 불러오기 실패 시 '수동 입력'으로 추천받을 수 있어요.\n"
                "- 가상 착용은 무료 GPU를 여러 사람이 나눠 쓰는 방식이라 대기와 하루 사용량 제한이 있어요."
            )
