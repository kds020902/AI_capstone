"""여러 화면이 같이 쓰는 값과 위젯: 체질 선택지, 업로드 처리, AI 후보 고르기, 가상 착용, 옷 정보 수정."""
from __future__ import annotations

import io
from pathlib import Path

import streamlit as st

from core.colors import COLOR_KO
from core.media import open_upright
from core.taxonomy import MAIN_CATEGORIES, SUBCATEGORIES, main_category_for_subcategory
from core.tryon import MAX_GARMENTS, space_addresses, tryon_default, tryon_kind, tryon_link, tryon_order, tryon_supported
from core.vision import SURE_CONFIDENCE

APP_NAME = "AI 옷장"
MATERIALS = ["", "cotton", "polyester", "denim", "wool", "linen", "nylon", "other"]
COLORS = list(COLOR_KO)

THERMAL_OPTIONS = ["추위를 잘 탐", "추위를 조금 탐", "보통", "더위를 조금 탐", "더위를 잘 탐"]
THERMAL_TO_SENSITIVITY = {
    "추위를 잘 탐": (1.0, 0.0),
    "추위를 조금 탐": (0.6, 0.1),
    "보통": (0.0, 0.0),
    "더위를 조금 탐": (0.1, 0.6),
    "더위를 잘 탐": (0.0, 1.0),
}
THERMAL_FEEDBACK = {None: "응답 안 함", -2: "매우 추움", -1: "약간 추움", 0: "적당함", 1: "약간 더움", 2: "매우 더움"}


def gender_ko(g):
    return "여성" if g == "female" else "남성"


def thermal_label(cold, heat):
    cold, heat = float(cold), float(heat)
    if cold >= 0.8:
        return "추위를 잘 탐"
    if cold >= 0.45:
        return "추위를 조금 탐"
    if heat >= 0.8:
        return "더위를 잘 탐"
    if heat >= 0.45:
        return "더위를 조금 탐"
    return "보통"


def upload_bytes(uploaded, rgb=False):
    """업로드 파일 → (바이트, 확장자). rgb=True 면 예전처럼 RGB로 다시 저장 (구매 후보 사진)."""
    suffix = Path(uploaded.name).suffix.lower() or ".jpg"
    if not rgb:
        return uploaded.getvalue(), suffix
    buf = io.BytesIO()
    fmt = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}.get(suffix, "JPEG")
    open_upright(uploaded).save(buf, format=fmt)
    return buf.getvalue(), suffix


def ai_candidate_pick(ai, key):
    """AI 상위 후보(최대 3개)를 고를 수 있게 보여 주고 고른 세부분류를 돌려준다.
    1등 확신이 낮으면 후보 중에서 직접 고르도록 안내한다 (웹 체험판과 같은 70% 기준)."""
    cands = ai.get("candidates") or [{"subcategory": ai["subcategory"], "confidence": ai["confidence"]}]
    best = cands[0]
    if ai.get("sure", best["confidence"] >= SURE_CONFIDENCE):
        st.success(f"AI 판단: {best['subcategory']} ({best['confidence'] * 100:.0f}%). 자동으로 골랐어요. "
                   "다르면 다른 후보나 아래 목록에서 고르세요.")
    else:
        st.warning(f"AI가 확실하지 않아요 (1등 {best['confidence'] * 100:.0f}%). "
                   "후보 중에서 맞는 것을 골라 주세요. 없으면 아래 목록에서 직접 고르세요.")
    conf = {c["subcategory"]: c["confidence"] for c in cands}
    return st.radio("AI 후보", list(conf), horizontal=True, key=key,
                    format_func=lambda sub: f"{sub} {conf[sub] * 100:.0f}%")


@st.dialog("가상 착용(예시)", width="large")
def tryon_dialog(app, items):
    """Hugging Face ZeroGPU Space(가상 착용)를 이 창 안에 띄우고, 고른 옷 사진을 10분짜리 서명 주소로 넘긴다."""
    supported = tryon_supported(items)
    choices = tryon_default(supported)
    if len(supported) > 1:
        picked = st.multiselect("입혀 볼 옷 (최대 2벌, 상의 → 하의 순서로 입혀요)", list(range(len(supported))),
                                default=[supported.index(x) for x in choices], max_selections=MAX_GARMENTS,
                                format_func=lambda i: f"{supported[i]['name']} ({supported[i]['category']})")
        choices = tryon_order([supported[i] for i in picked])
    garments = [(app.media.signed_url(x.get("image_path"), seconds=600), tryon_kind(x.get("category"))) for x in choices]
    embed, page = space_addresses(app.tryon_url)
    st.caption("사람 사진에 옷을 입혀 보는 AI 예시 이미지예요. 실제 사이즈·핏은 반영되지 않아요.")
    if any(g is None for g, _ in garments):
        st.info("사진이 없거나 넘길 수 없는 옷이 있어요 (스타터 옷장·로컬 실행 등). 아래 창에서 옷 사진을 직접 올려 주세요.")
    st.link_button("새 창(Hugging Face)에서 열기 · 권장", tryon_link(page, garments), type="primary")
    st.caption("새 창에서는 내 무료 GPU 사용량으로 실행돼요 (하루 비로그인 약 2분, Hugging Face 무료 계정 로그인 시 약 5분, "
               "첫 사용 24시간 뒤 다시 채워짐). 아래 창은 바로 써 볼 수 있지만 Hugging Face 로그인이 전달되지 않아 "
               "사용량이 더 빨리 바닥날 수 있어요. 무료 GPU라 대기열이 생길 수 있어요.")
    if hasattr(st, "iframe"):      # 새 Streamlit (components.iframe 은 없어질 예정)
        st.iframe(tryon_link(embed, garments), height=820)
    else:
        import streamlit.components.v1 as components
        components.iframe(tryon_link(embed, garments), height=820, scrolling=True)
    st.caption("올린 사람 사진은 AI 옷장 DB에 저장하지 않아요. 옷 사진 주소는 10분 동안만 열려요.")


def tryon_button(app, items, key):
    """가상 착용 Space 주소가 설정돼 있고, 입힐 수 있는 옷(상의·하의·원피스·아우터)이 있을 때만 버튼을 보인다."""
    if app.tryon_url and tryon_supported(items):
        if st.button("👗 가상 착용(예시)", key=f"tryon_{key}"):
            tryon_dialog(app, items)


@st.dialog("정보 수정")
def edit_item_dialog(app, item, user_id):
    """저장한 옷의 이름·종류·색·두께 등을 고친다 (사진·AI 분류 기록·계절 설정은 그대로)."""
    iid, sub0 = item["id"], item.get("subcategory")
    subs = SUBCATEGORIES if (not sub0 or sub0 in SUBCATEGORIES) else SUBCATEGORIES + [sub0]
    name = st.text_input("이름", value=item["name"], key=f"ed_name_{iid}")
    c1, c2 = st.columns(2)
    sub = c1.selectbox("종류", subs, index=subs.index(sub0) if sub0 in subs else 0, key=f"ed_sub_{iid}")
    cat0 = item["category"] if sub == sub0 else (main_category_for_subcategory(sub) or item["category"])
    cat = c2.selectbox("대분류", MAIN_CATEGORIES, key=f"ed_cat_{iid}_{sub}",   # 종류를 바꾸면 대분류가 따라옴
                       index=MAIN_CATEGORIES.index(cat0) if cat0 in MAIN_CATEGORIES else 0)
    color0 = item.get("color") or ""
    colors = COLORS if color0 in COLOR_KO else [color0] + COLORS                 # 예전에 직접 쓴 색도 그대로 둘 수 있게
    c3, c4 = st.columns(2)
    color = c3.selectbox("색", colors, index=colors.index(color0), key=f"ed_color_{iid}",
                         format_func=lambda c: COLOR_KO.get(c) or (f"{c} (지금 값)" if c else "없음"))
    warmth = c4.slider("두께/보온", 1, 5, int(item["warmth"]), key=f"ed_warmth_{iid}")
    mat0 = item.get("material") or ""
    mats = MATERIALS if mat0 in MATERIALS else MATERIALS + [mat0]
    material = st.selectbox("소재", mats, index=mats.index(mat0), key=f"ed_mat_{iid}", format_func=lambda m: m or "모름")
    rain = st.checkbox("비 오는 날 부담이 적음", value=bool(item["rain_ok"]), key=f"ed_rain_{iid}")
    notes = st.text_input("설명", value=item.get("notes") or "", key=f"ed_notes_{iid}", placeholder="예: 기모 안감, 오버핏")
    st.caption("사진·AI 분류 기록·계절 설정은 그대로 둬요. 계절은 '계절 수정'에서 바꿔요.")
    if st.button("저장", type="primary", key=f"ed_save_{iid}", disabled=not name.strip()):
        app.store.update_wardrobe_item(iid, user_id, name.strip(), cat, sub, color, warmth, rain, material, notes.strip())
        st.rerun()
