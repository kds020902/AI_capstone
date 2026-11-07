"""내 옷장: 옷장 목록(계절 정리·수정·비활성화·삭제), 스타터 옷장, 직접 추가."""
from __future__ import annotations

from datetime import date

import streamlit as st

from core.care import get_care_guide
from core.taxonomy import (
    MAIN_CATEGORIES, SEASONS, current_season, item_seasons, manual_seasons, season_label, seasons_for,
)
from screens import direct_add
from screens.common import edit_item_dialog, gender_ko, tryon_button


def render(app):
    st.title("내 옷장")
    user = app.need_user()

    st.caption(f"현재 프로필: {user['name']} · {gender_ko(user['gender'])}")
    tab1, tab2, tab3 = st.tabs(["옷장 목록", "30개 스타터 옷장", "직접 추가"])

    with tab1:
        items = app.active_items(user["id"])
        season_now = current_season(date.today().month)
        with st.expander(f"계절 정리 · 지금은 {season_now}"):
            season = st.radio("계절", SEASONS, index=SEASONS.index(season_now), horizontal=True, key="tidy_season")
            off = [x for x in items if season not in item_seasons(x)]
            back = [x for x in app.inactive_items(user["id"]) if season in item_seasons(x)]
            c1, c2 = st.columns(2)
            if c1.button(f"{season}에 안 입는 옷 {len(off)}벌 넣어두기", disabled=not off):
                app.store.set_items_active([x["id"] for x in off], user["id"], False)
                st.rerun()
            if c2.button(f"넣어 둔 옷 중 {season} 옷 {len(back)}벌 꺼내기", disabled=not back):
                app.store.set_items_active([x["id"] for x in back], user["id"], True)
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
                        app.show_image(item["image_path"], thumb=True)
                        st.markdown(f"### {item['name']}")
                        st.caption(
                            f"{item['category']} / {item.get('subcategory') or '세부분류 없음'} · "
                            f"두께 {item['warmth']}/5 · {season_label(item_seasons(item))}"
                            + (" (직접 지정)" if manual_seasons(item) else "")
                        )
                        st.write(f"색상: {item['color'] or '-'} · 비 적합: {'O' if item['rain_ok'] else 'X'}")
                        if item.get("material"):
                            st.caption(f"세탁: {get_care_guide(item['material'])['text']}")
                        if item.get("notes"):
                            st.caption(f"설명: {item['notes']}")
                        tryon_button(app, [item], f"item_{item['id']}")
                        with st.container(horizontal=True):
                            if st.button("정보 수정", key=f"edit_{item['id']}"):
                                edit_item_dialog(app, item, user["id"])
                            with st.popover("계절 수정"):
                                picked = st.multiselect("입는 계절", SEASONS, default=item_seasons(item),
                                                        key=f"season_{item['id']}")
                                if st.button("저장", key=f"season_save_{item['id']}"):
                                    same_as_auto = picked == seasons_for(item.get("subcategory"))
                                    app.store.update_item_seasons(item["id"], user["id"], None if same_as_auto else picked)
                                    st.rerun()
                                st.caption(f"자동: {season_label(seasons_for(item.get('subcategory')))} (옷 종류 기준). "
                                           "전부 지우고 저장하면 자동으로 돌아가요.")
                        c_a, c_b = st.columns(2)
                        if c_a.button("비활성화", key=f"off_{item['id']}"):
                            app.store.deactivate_item(item["id"], user["id"])
                            st.rerun()
                        if c_b.button("삭제", key=f"del_{item['id']}"):
                            app.store.soft_delete_item(item["id"], user["id"])
                            st.rerun()

        # 비활성화한 옷 다시 활성화
        inactive = app.inactive_items(user["id"])
        with st.expander(f"넣어 둔 옷 · 비활성화 ({len(inactive)}개)"):
            if not inactive:
                st.caption("넣어 둔 옷이 없습니다.")
            for item in inactive:
                c_a, c_b = st.columns([4, 1])
                c_a.write(f"{item['name']} · {item['category']} / {item.get('subcategory') or '-'} · "
                          f"{season_label(item_seasons(item))}")
                if c_b.button("다시 활성화", key=f"on_{item['id']}"):
                    app.store.reactivate_item(item["id"], user["id"])
                    st.rerun()

    with tab2:
        st.subheader(f"{gender_ko(user['gender'])} 캐주얼 스타터 옷장 30개")
        st.write(
            "일일이 옷을 등록하지 않고 테스트를 시작할 수 있도록 자주 쓰이는 캐주얼 아이템 30개를 넣습니다. "
            "실제로 보유하지 않은 항목은 목록에서 비활성화/삭제하세요."
        )
        if st.button("프로필 성별 기준 30개 추가", type="primary"):
            added = app.store.add_starter_wardrobe(user["id"])
            st.success(f"{added}개를 추가했습니다. 이미 있던 이름은 중복 추가하지 않았습니다.")
            st.rerun()

    with tab3:
        direct_add.render(app, user)
