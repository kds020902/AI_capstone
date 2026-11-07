"""사기 전에 맞춰보기: 구매 후보를 지금 옷장과 맞춰 보고, 샀으면 옷장에 넣는다."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core.colors import COLOR_KO, normalize_color
from core.media import open_upright
from core.shopping import candidate_report
from core.taxonomy import (
    PURPOSES, RULES, SEASONS, SUBCATEGORIES, current_season, main_category_for_subcategory, season_label,
)
from core.vision import classify_clothing_image, dominant_color_name
from screens.common import ai_candidate_pick, tryon_button, upload_bytes


@st.cache_data(show_spinner=False, max_entries=64)
def cached_candidate_report(cand, items, cold, heat):
    return candidate_report(cand, items, cold, heat)


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
        up = st.file_uploader("상품 사진 또는 캡처 (JPG/PNG)", type=["jpg", "jpeg", "png"], key="cand_upload")
        cand_ai, cand_color, cand_pick = None, "", None
        if up:
            cand_image = open_upright(up)
            st.image(cand_image, width=220)
            cand_color = dominant_color_name(cand_image) or ""
            # 다시 그릴 때마다 분류하지 않도록 지금 사진의 결과 하나만 둔다 (사진마다 결과가 쌓이지 않게)
            if (st.session_state.get("cand_ai") or (None,))[0] != up.file_id:
                st.session_state.cand_ai = (up.file_id, classify_clothing_image(cand_image))
            cand_ai = st.session_state.cand_ai[1]
            if cand_ai.get("ok"):
                cand_pick = ai_candidate_pick(cand_ai, key=f"cand_pick_{up.file_id}")
                st.caption(f"색 추정: {COLOR_KO.get(cand_color, cand_color)}")
            else:
                st.caption(cand_ai["message"])
        ai_sub = cand_ai.get("subcategory") if cand_ai and cand_ai.get("ok") else None   # 기록용: AI 1등 답
        c1, c2 = st.columns(2)
        cand_sub = c1.selectbox("종류", SUBCATEGORIES,
                                index=SUBCATEGORIES.index(cand_pick) if cand_pick in SUBCATEGORIES else 0,
                                key=f"cand_sub_{cand_pick}")
        colors = list(COLOR_KO)
        cand_color = c2.selectbox("색", colors, index=colors.index(cand_color) if cand_color in colors else 0,
                                  format_func=lambda c: COLOR_KO[c], key=f"cand_color_{cand_color}")
        rule = RULES[cand_sub]
        c3, c4 = st.columns(2)
        cand_warmth = c3.slider("두께/보온", 1, 5, rule["default_warmth"], key=f"cand_w_{cand_sub}")
        cand_rain = c4.checkbox("비 오는 날 부담이 적음", value=rule["default_rain_ok"], key=f"cand_r_{cand_sub}")
        cand_name = st.text_input("이름", value=f"{COLOR_KO[cand_color]} {cand_sub}", key=f"cand_name_{cand_color}_{cand_sub}")
        cand_link = st.text_input("상품 링크 (선택, 메모용)", key="cand_link")
        if st.button("구매 후보로 저장", type="primary", disabled=not cand_name.strip()):
            path = ""
            if up:
                data, suffix = upload_bytes(up, rgb=True)
                path = app.media.save(user["id"], "cand_", data, suffix)
            app.store.create_candidate(user["id"], cand_name.strip(), main_category_for_subcategory(cand_sub), cand_sub,
                                cand_color, cand_warmth, cand_rain, image_path=path, link=cand_link.strip(),
                                ai_subcategory=ai_sub, ai_confidence=cand_ai.get("confidence") if cand_ai else None)
            st.rerun()

    if not candidates:
        st.info("아직 구매 후보가 없어요. 위에서 상품 사진을 올려 보세요.")
        return
    season_now = current_season(date.today().month)
    c1, c2 = st.columns(2)
    view_season = c1.radio("코디를 볼 계절", SEASONS, index=SEASONS.index(season_now), horizontal=True)
    view_purpose = c2.radio("외출 목적", PURPOSES, horizontal=True)
    cold, heat = float(user["cold_sensitivity"]), float(user["heat_sensitivity"])
    owned = {x["id"]: x["name"] for x in items}
    VERDICT_BOX = {"dup": st.warning, "none": st.error, "gap": st.success, "fit": st.info}

    for cand in candidates:
        rep = cached_candidate_report(cand, items, cold, heat)
        with st.container(border=True):
            c_img, c_body = st.columns([1, 3])
            with c_img:
                app.show_image(cand["image_path"], thumb=True)
                tryon_button(app, [cand], f"cand_{cand['id']}")
            with c_body:
                st.markdown(f"### {cand['name']}")
                st.caption(f"{cand['category']} / {cand['subcategory']} · {COLOR_KO.get(normalize_color(cand['color']), cand['color'])} · "
                           f"두께 {cand['warmth']}/5 · {season_label(rep['seasons'])}"
                           + (f" · [상품 링크]({cand['link']})" if cand["link"] else ""))
                VERDICT_BOX[rep["verdict"][0]](rep["verdict"][1])
                if rep["similar"]["same_type"]:
                    st.caption("같은 종류로 가진 옷: " + ", ".join(x["name"] for x in rep["similar"]["same_type"]))
                if rep["partners"]:
                    st.caption(f"어울리는 내 옷 {len(rep['partners'])}벌: "
                               + ", ".join(owned[i] for i in rep["partners"][:8] if i in owned)
                               + (" …" if len(rep["partners"]) > 8 else ""))
            gain = {(x["season"], x["purpose"]) for x in rep["improves"]}
            table = pd.DataFrame([[score_cell(rep["cells"][(s, p)], (s, p) in gain) for p in PURPOSES] for s in SEASONS],
                                 index=SEASONS, columns=PURPOSES)
            st.caption("상황별 최고 코디 점수 (– 는 이 옷을 입기 어려운 상황, ▲ 는 이 옷이 있으면 점수가 오르는 상황)")
            st.dataframe(table, width="stretch")
            cell = rep["cells"][(view_season, view_purpose)]
            if cell["results"]:
                st.markdown(f"**{view_season} · {view_purpose} 코디 (체감 {cell['temp']}℃ 기준)**")
                for r in cell["results"]:
                    st.write(f"- {r['score'] * 100:.0f}점 · " + " + ".join(p["name"] for p in r["pieces"]))
            else:
                st.caption(f"{view_season} · {view_purpose}에는 이 옷으로 만들 코디가 없어요.")
            c_a, c_b = st.columns(2)
            if c_a.button("샀어요 · 옷장에 넣기", key=f"buy_{cand['id']}"):
                app.store.buy_candidate(cand["id"], user["id"])
                st.toast(f"{cand['name']}을(를) 옷장에 넣었어요.")
                st.rerun()
            if c_b.button("후보에서 지우기", key=f"cdel_{cand['id']}"):
                app.store.delete_candidate(cand["id"], user["id"])
                st.rerun()
