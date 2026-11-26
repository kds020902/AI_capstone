"""내 옷장: 옷장 목록(계절 정리·수정·비활성화·삭제), 스타터 옷장, 직접 추가."""
from __future__ import annotations

from datetime import date

import streamlit as st

from core.care import get_care_guide
from core.colors import COLOR_KO
from core.taxonomy import (
    MAIN_CATEGORIES, SEASONS, current_season, item_seasons, manual_seasons, season_label, seasons_for,
)
from screens import direct_add
from screens.common import edit_item_dialog, gender_ko, info_icon, square_photo, tryon_panel


def render(app):
    st.title("내 옷장")
    user = app.need_user()

    st.caption(f"현재 프로필: {user['name']} · {gender_ko(user['gender'])}")
    names = ["옷장 목록", "30개 스타터 옷장", "직접 추가"] + (["한번 입어보기"] if app.tryon_url else [])
    tab1, tab2, tab3, *tab_tryon = st.tabs(names)      # 가상 착용 Space 주소가 설정됐을 때만 '한번 입어보기' 탭
    with tab1:
        items = app.active_items(user["id"])
        _season_tidy(app, user, items)
        filtered, filters = _filtered(items)
        if not filtered:
            st.info("등록된 의류가 없습니다." if not items else "조건에 맞는 옷이 없어요. 카테고리·계절·검색어를 바꿔 보세요.")
        else:
            page, pages = _current_page(len(filtered), filters)
            start = (page - 1) * PAGE_SIZE
            shown = filtered[start:start + PAGE_SIZE]
            with st.container(key=LIST_KEY):
                st.caption(f"{len(filtered)}벌 중 {start + 1}–{start + len(shown)}번째")
                for row in range(0, len(shown), COLUMNS):      # 줄마다 칸을 새로 만들어 카드 줄이 맞게
                    for col, item in zip(st.columns(COLUMNS), shown[row:row + COLUMNS]):
                        with col:
                            _item_card(app, user, item)
            _pager(page, pages)
        _inactive_list(app, user)
    with tab2:
        _starter_tab(app, user)
    with tab3:
        direct_add.render(app, user)
    for tab in tab_tryon:
        with tab:
            tryon_panel(app, app.active_items(user["id"]))


def _season_tidy(app, user, items):
    """지금 계절에 안 입는 옷은 한 번에 넣어 두고, 넣어 둔 옷 중 지금 계절 옷은 한 번에 꺼낸다."""
    season_now = current_season(date.today().month)
    with st.expander(f"계절 정리 · 지금은 {season_now}"):
        season = st.radio("계절", SEASONS, index=SEASONS.index(season_now), horizontal=True, key="tidy_season",
                          help="넣어 둔 옷은 추천에 쓰지 않아요. 아래 '넣어 둔 옷'에서 한 벌씩 꺼낼 수도 있어요.")
        off = [x for x in items if season not in item_seasons(x)]
        back = [x for x in app.inactive_items(user["id"]) if season in item_seasons(x)]
        c1, c2 = st.columns(2)
        if c1.button(f"{season}에 안 입는 옷 {len(off)}벌 넣어두기", disabled=not off):
            app.store.set_items_active([x["id"] for x in off], user["id"], False)
            st.rerun()
        if c2.button(f"넣어 둔 옷 중 {season} 옷 {len(back)}벌 꺼내기", disabled=not back):
            app.store.set_items_active([x["id"] for x in back], user["id"], True)
            st.rerun()


def _filtered(items):
    """카테고리·계절·이름(세부분류) 검색으로 거른 목록. → (목록, 거르기 조건)"""
    c1, c2, c3 = st.columns(3)
    category = c1.selectbox("카테고리", ["전체"] + MAIN_CATEGORIES)
    season = c2.selectbox("계절", ["전체"] + SEASONS)
    q = c3.text_input("이름/세부분류 검색").strip().lower()
    return [x for x in items
            if (category == "전체" or x["category"] == category)
            and (season == "전체" or season in item_seasons(x))
            and (not q or q in x["name"].lower() or q in str(x.get("subcategory") or "").lower())], (category, season, q)


PAGE_SIZE = 9        # 한 쪽에 보이는 옷 (3벌씩 3줄)
COLUMNS = 3
PAGE_KEY = "wardrobe_page"
LIST_KEY = "wardrobe_list"
# 맨 아래에서 쪽을 넘기면 새 쪽의 끝이 보이므로, 쪽을 바꾼 그 한 번만 목록 맨 위로 올린다 (Streamlit 에 스크롤 기능이 없어 짧은 JS).
# 위쪽 고정 막대(약 60px)에 가리지 않게 조금 더 위로 둔다.
SCROLL_TO_LIST_JS = """<script>
setTimeout(() => {{
  const main = document.querySelector('[data-testid="stMain"]');
  const list = document.querySelector('.st-key-{key}');
  if (main && list) main.scrollTo({{top: main.scrollTop + list.getBoundingClientRect().top - 80, behavior: 'instant'}});
}}, 60);
</script><!-- {nonce} -->"""


def _current_page(n, filters):
    """지금 쪽 번호(1부터)와 전체 쪽 수. 거르기 조건이 바뀌면 1쪽으로, 옷이 줄어 쪽이 없어지면 마지막 쪽으로."""
    pages = max(1, -(-n // PAGE_SIZE))
    if st.session_state.get(PAGE_KEY + "_filters") != filters:
        st.session_state[PAGE_KEY + "_filters"] = filters
        st.session_state[PAGE_KEY] = 1
    page = min(max(1, st.session_state.get(PAGE_KEY, 1)), pages)
    st.session_state[PAGE_KEY] = page
    return page, pages


def page_numbers(page, pages, around=2):
    """보여 줄 쪽 번호: 처음·끝·지금 쪽 앞뒤 2쪽, 사이가 비면 None(…)."""
    keep = sorted({1, pages, *range(page - around, page + around + 1)} & set(range(1, pages + 1)))
    out = []
    for a, b in zip([None] + keep, keep):
        if a is not None and b - a > 1:
            out.append(None)
        out.append(b)
    return out


def _go_page(n):
    st.session_state[PAGE_KEY] = n
    st.session_state[PAGE_KEY + "_moved"] = st.session_state.get(PAGE_KEY + "_moved", 0) + 1


def _pager(page, pages):
    """맨 아래 쪽 넘기기: ‹ 이전 · 쪽 번호 · 다음 ›. 한 쪽이면 그리지 않는다."""
    if pages <= 1:
        return
    with st.container(horizontal=True, horizontal_alignment="center", gap="small"):
        st.button("이전", icon=":material/chevron_left:", key="page_prev", disabled=page == 1,
                  on_click=_go_page, args=(page - 1,))
        for i, n in enumerate(page_numbers(page, pages)):
            if n is None:
                st.markdown("…")
            else:
                st.button(str(n), key=f"page_{n}", type="primary" if n == page else "secondary",
                          on_click=_go_page, args=(n,), help=f"{n}쪽" if n != page else "지금 보는 쪽")
        st.button("다음", icon=":material/chevron_right:", key="page_next", disabled=page == pages,
                  on_click=_go_page, args=(page + 1,))
    moved = st.session_state.get(PAGE_KEY + "_moved", 0)
    if moved != st.session_state.get(PAGE_KEY + "_scrolled"):   # 쪽을 바꾼 직후 한 번만
        st.session_state[PAGE_KEY + "_scrolled"] = moved
        if moved:
            st.html(SCROLL_TO_LIST_JS.format(key=LIST_KEY, nonce=moved), unsafe_allow_javascript=True)


def _item_card(app, user, item):
    """사진 → 이름 → 종류·두께·계절 한 줄 → 색·비 한 줄 → 세탁·설명 아이콘(누르거나 올리면 내용) → 버튼."""
    with st.container(border=True, gap="small"):
        st.image(square_photo(app, item.get("image_path")), width="stretch",
                 alt=item["name"] if item.get("image_path") else f"{item['name']} (사진 없음)")
        st.markdown(f"##### {item['name']}")
        seasons = season_label(item_seasons(item)) + (" (직접 지정)" if manual_seasons(item) else "")
        st.caption(f"{item['category']} / {item.get('subcategory') or '세부분류 없음'} · 두께 {item['warmth']}/5 · {seasons}")
        color = COLOR_KO.get(item["color"] or "", item["color"] or "-")
        st.markdown(f"색상 {color} · 비 오는 날 {'괜찮음' if item['rain_ok'] else '피하기'}")
        tips = []
        if item.get("material"):
            care = get_care_guide(item["material"])
            tips.append((":material/local_laundry_service:", "세탁", f"세탁: {care['text']}  \n({care['source']})"))
        if item.get("notes"):
            tips.append((":material/sticky_note_2:", "설명", f"설명: {item['notes']}"))
        if tips:
            with st.container(horizontal=True, gap="small"):
                for icon, label, text in tips:
                    info_icon(label, text, icon, key=f"tip_{label}_{item['id']}")
        with st.container(horizontal=True, gap="small"):
            if st.button("정보 수정", key=f"edit_{item['id']}"):
                edit_item_dialog(app, item, user["id"])
            with st.popover("계절 수정"):
                _season_editor(app, user, item)
            if st.button("비활성화", key=f"off_{item['id']}", help="넣어 둔 옷이 돼요. 추천에 안 쓰고, 아래 '넣어 둔 옷'에서 다시 꺼낼 수 있어요."):
                app.store.deactivate_item(item["id"], user["id"])
                st.rerun()
            if st.button("삭제", key=f"del_{item['id']}", help="옷장 목록에서 지워요. 이 옷이 든 저장 코디는 그대로 남아요."):
                app.store.soft_delete_item(item["id"], user["id"])
                st.rerun()


def _season_editor(app, user, item):
    auto = seasons_for(item.get("subcategory"))
    picked = st.multiselect("입는 계절", SEASONS, default=item_seasons(item), key=f"season_{item['id']}",
                            help=f"자동: {season_label(auto)} (옷 종류 기준). 전부 지우고 저장하면 자동으로 돌아가요.")
    if st.button("저장", key=f"season_save_{item['id']}"):
        app.store.update_item_seasons(item["id"], user["id"], None if picked == auto else picked)
        st.rerun()


def _inactive_list(app, user):
    """넣어 둔(비활성화한) 옷을 한 벌씩 다시 꺼낸다."""
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


def _starter_tab(app, user):
    st.subheader(f"{gender_ko(user['gender'])} 캐주얼 스타터 옷장 30개",
                 help="일일이 옷을 등록하지 않고 테스트를 시작할 수 있도록 자주 쓰이는 캐주얼 아이템 30개를 넣습니다. "
                      "실제로 보유하지 않은 항목은 목록에서 비활성화/삭제하세요.")
    if st.button("프로필 성별 기준 30개 추가", type="primary"):
        added = app.store.add_starter_wardrobe(user["id"])
        st.success(f"{added}개를 추가했습니다. 이미 있던 이름은 중복 추가하지 않았습니다.")
        st.rerun()
