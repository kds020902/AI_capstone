"""사기 전에 맞춰보기: 구매 후보를 지금 옷장과 맞춰 보고, 샀으면 옷장에 넣는다."""
from __future__ import annotations

import uuid
from datetime import date

import pandas as pd
import streamlit as st

from core.colors import COLOR_KO, normalize_color
from core.media import open_upright
from core.shopping import baseline, candidate_report
from core.taxonomy import (
    PURPOSES, RULES, SEASONS, SUBCATEGORIES, current_season, main_category_for_subcategory, season_label,
)
from core.vision import classify_clothing_image, dominant_color_name
from screens.common import ai_candidate_pick, tryon_button, upload_bytes


@st.cache_data(show_spinner=False, max_entries=16)
def cached_baseline(items, cold, heat):
    """후보 없이 지금 옷장의 상황별 최고 점수 (같은 옷장·체질이면 후보끼리 같이 씀)."""
    return baseline(items, cold, heat)


@st.cache_data(show_spinner=False, max_entries=64)
def cached_candidate_report(cand, items, cold, heat):
    return candidate_report(cand, items, cold, heat, cached_baseline(items, cold, heat))


def score_cell(cell, improved):
    if cell["best"] is None:
        return "–"
    return f"{cell['best'] * 100:.0f}점" + (" ▲" if improved else "")


def render(app):
    st.title("사기 전에 맞춰보기")
    st.caption("쇼핑몰 상품 사진이나 화면 캡처를 올리면 지금 옷장과 맞춰 봐요. 옷장에는 넣지 않고 '구매 후보'로 따로 둬요.")
    user = app.need_user()
    items = app.active_items(user["id"])
    candidates = app.store.list_candidates(user["id"])
    with st.expander("구매 후보 추가", expanded=not candidates):
        _add_candidate(app, user)
    if not candidates:
        st.info("아직 구매 후보가 없어요. 위에서 상품 사진을 올려 보세요.")
        return

    season_now = current_season(date.today().month)
    c1, c2 = st.columns(2)
    view = (c1.radio("코디를 볼 계절", SEASONS, index=SEASONS.index(season_now), horizontal=True),
            c2.radio("외출 목적", PURPOSES, horizontal=True))
    for cand in candidates:
        rep = cached_candidate_report(cand, items, float(user["cold_sensitivity"]), float(user["heat_sensitivity"]))
        _candidate_card(app, user, cand, rep, items, view)


def _photo_ai(up):
    """올린 상품 사진 → (AI 결과, 대표색, 고른 AI 후보). 같은 사진은 다시 분류하지 않고, 지금 사진 결과 하나만 둔다."""
    image = open_upright(up)
    st.image(image, width=220)
    color = dominant_color_name(image) or ""
    if (st.session_state.get("cand_ai") or (None,))[0] != up.file_id:
        st.session_state.cand_ai = (up.file_id, classify_clothing_image(image))
    ai = st.session_state.cand_ai[1]
    pick = None
    if ai.get("ok"):
        pick = ai_candidate_pick(ai, key=f"cand_pick_{up.file_id}")
        st.caption(f"색 추정: {COLOR_KO.get(color, color)}")
    else:
        st.caption(ai["message"])
    return ai, color, pick


def _add_candidate(app, user):
    up = st.file_uploader("상품 사진 또는 캡처 (JPG/PNG)", type=["jpg", "jpeg", "png"], key="cand_upload")
    ai, color, pick = _photo_ai(up) if up else (None, "", None)
    ai_ok = bool(ai and ai.get("ok"))
    c1, c2 = st.columns(2)
    sub = c1.selectbox("종류", SUBCATEGORIES, index=SUBCATEGORIES.index(pick) if pick in SUBCATEGORIES else 0,
                       key=f"cand_sub_{pick}")
    colors = list(COLOR_KO)
    color = c2.selectbox("색", colors, index=colors.index(color) if color in colors else 0,
                         format_func=lambda c: COLOR_KO[c], key=f"cand_color_{color}")
    rule = RULES[sub]
    c3, c4 = st.columns(2)
    warmth = c3.slider("두께/보온", 1, 5, rule["default_warmth"], key=f"cand_w_{sub}")
    rain = c4.checkbox("비 오는 날 부담이 적음", value=rule["default_rain_ok"], key=f"cand_r_{sub}")
    name = st.text_input("이름", value=f"{COLOR_KO[color]} {sub}", key=f"cand_name_{color}_{sub}")
    link = st.text_input("상품 링크 (선택, 메모용)", key="cand_link")
    if st.button("구매 후보로 저장", type="primary", disabled=not name.strip()):
        try:
            path = _candidate_photo(app, user, up) if up else ""
            app.store.create_candidate(user["id"], name.strip(), main_category_for_subcategory(sub), sub, color, warmth,
                                       rain, image_path=path, link=link.strip(),
                                       ai_subcategory=ai.get("subcategory") if ai_ok else None,   # 기록용: AI 1등 답
                                       ai_confidence=ai.get("confidence") if ai else None,
                                       request_id=_candidate_request())
        except Exception as e:
            st.error(f"저장하지 못했어요 ({type(e).__name__}). 잠시 뒤 다시 눌러 주세요. 다시 누르면 내용을 고쳤더라도 "
                     "지금 내용으로 후보 하나만 저장돼요 (앞의 저장이 사실은 됐다면 그 후보를 지금 내용으로 고쳐요).")
            return
        st.session_state.cand_request = st.session_state.cand_photo = None
        st.rerun()


def _candidate_photo(app, user, up):
    """올린 상품 사진은 파일마다 한 번만 저장소에 올린다. 저장이 실패해 다시 누르거나 이름 등을 고쳐도 그 경로를 다시 쓴다
    (사진 이름이 고정이라 응답만 못 받은 사진도 같은 파일에 덮어씀)."""
    p = st.session_state.get("cand_photo")
    if not p or p["file_id"] != up.file_id:
        p = st.session_state.cand_photo = {"file_id": up.file_id, "key": str(uuid.uuid4()), "path": ""}
    if not p["path"]:
        data, suffix = upload_bytes(up, rgb=True)
        p["path"] = app.media.save(user["id"], "cand_", data, suffix, key=p["key"])
    return p["path"]


def _candidate_request():
    """구매 후보 등록 작업 id. 저장에 성공할 때까지 같은 id를 쓴다: 저장이 오류로 끝난 뒤 이름·링크·사진을 고치거나
    A→B→A로 바꿔 다시 눌러도 같은 등록이라, 앞의 저장이 사실은 됐다면 그 후보를 지금 내용으로 고치고(저장소의 요청 id
    규칙) 아니면 새로 넣는다. 저장에 성공하면 비워서, 다음 저장(같은 사진이어도)은 새 후보가 된다."""
    req = st.session_state.get("cand_request")
    if not req:
        req = st.session_state.cand_request = str(uuid.uuid4())
    return req



VERDICT_BOX = {"dup": st.warning, "none": st.error, "gap": st.success, "fit": st.info}


def _candidate_card(app, user, cand, rep, items, view):
    with st.container(border=True):
        c_img, c_body = st.columns([1, 3])
        with c_img:
            app.show_image(cand["image_path"], thumb=True)
            tryon_button(app, [cand], f"cand_{cand['id']}")
        with c_body:
            _candidate_summary(cand, rep, {x["id"]: x["name"] for x in items})
        gain = {(x["season"], x["purpose"]) for x in rep["improves"]}
        table = pd.DataFrame([[score_cell(rep["cells"][(s, p)], (s, p) in gain) for p in PURPOSES] for s in SEASONS],
                             index=SEASONS, columns=PURPOSES)
        st.caption("상황별 최고 코디 점수 (– 는 이 옷을 입기 어려운 상황, ▲ 는 이 옷이 있으면 점수가 오르는 상황)")
        st.dataframe(table, width="stretch")
        _example_outfits(rep["cells"][view], *view)
        c_a, c_b = st.columns(2)
        if c_a.button("샀어요 · 옷장에 넣기", key=f"buy_{cand['id']}"):
            app.store.buy_candidate(cand["id"], user["id"])
            st.toast(f"{cand['name']}을(를) 옷장에 넣었어요.")
            st.rerun()
        if c_b.button("후보에서 지우기", key=f"cdel_{cand['id']}"):
            app.store.delete_candidate(cand["id"], user["id"])
            st.rerun()


def _candidate_summary(cand, rep, owned):
    st.markdown(f"### {cand['name']}")
    color = COLOR_KO.get(normalize_color(cand["color"]), cand["color"])
    st.caption(f"{cand['category']} / {cand['subcategory']} · {color} · 두께 {cand['warmth']}/5 · "
               f"{season_label(rep['seasons'])}" + (f" · [상품 링크]({cand['link']})" if cand["link"] else ""))
    VERDICT_BOX[rep["verdict"][0]](rep["verdict"][1])
    if rep["similar"]["same_type"]:
        st.caption("같은 종류로 가진 옷: " + ", ".join(x["name"] for x in rep["similar"]["same_type"]))
    if rep["partners"]:
        st.caption(f"어울리는 내 옷 {len(rep['partners'])}벌: "
                   + ", ".join(owned[i] for i in rep["partners"][:8] if i in owned)
                   + (" …" if len(rep["partners"]) > 8 else ""))


def _example_outfits(cell, season, purpose):
    if not cell["results"]:
        st.caption(f"{season} · {purpose}에는 이 옷으로 만들 코디가 없어요.")
        return
    st.markdown(f"**{season} · {purpose} 코디 (체감 {cell['temp']}℃ 기준)**")
    for r in cell["results"]:
        st.write(f"- {r['score'] * 100:.0f}점 · " + " + ".join(p["name"] for p in r["pieces"]))
