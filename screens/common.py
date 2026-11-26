"""여러 화면이 같이 쓰는 값과 위젯: 체질 선택지, 업로드 처리, AI 후보 고르기, 가상 착용, 옷 정보 수정."""
from __future__ import annotations

import inspect
import io
from pathlib import Path

import streamlit as st

from core.colors import COLOR_KO
from core.eligibility import THERMAL_TYPES
from core.media import PhotoCache, open_upright, square_jpeg
from core.taxonomy import MAIN_CATEGORIES, SUBCATEGORIES, main_category_for_subcategory
from core.tryon import MAX_GARMENTS, space_addresses, tryon_default, tryon_kind, tryon_link, tryon_order, tryon_supported
from core.vision import SURE_CONFIDENCE

APP_NAME = "AI 옷장"
# 서비스 로고 Fit (흰 둥근 판 위 검은 글자라 밝은·어두운 모드 모두에서 보인다). 가로형은 사이드바·로그인, 정사각형은 탭 아이콘.
ASSETS = Path(__file__).resolve().parents[1] / "assets"
LOGO = str(ASSETS / "fit_logo.png")
LOGO_ICON = str(ASSETS / "fit_icon.png")
MATERIALS = ["", "cotton", "polyester", "denim", "wool", "linen", "nylon", "other"]
COLORS = list(COLOR_KO)

THERMAL_TO_SENSITIVITY = THERMAL_TYPES          # 체감 성향 → (추위, 더위) 민감도 (core/eligibility.py 한 곳)
THERMAL_OPTIONS = list(THERMAL_TYPES)
THERMAL_FEEDBACK = {None: "응답 안 함", -2: "매우 추움", -1: "약간 추움", 0: "적당함", 1: "약간 더움", 2: "매우 더움"}


NAV_KEY = "nav_page"     # 사이드바 메뉴(app.py)의 위젯 key. 다른 화면으로 보내는 버튼이 이 값을 바꾼다.


def go_to_page(label, page, key, primary=False):
    """다른 메뉴로 가는 버튼 (예: 옷이 부족할 때 '내 옷장'). 누르면 사이드바 메뉴가 그 화면으로 바뀐다."""
    def _go():
        st.session_state[NAV_KEY] = page
    st.button(label, key=key, on_click=_go, type="primary" if primary else "secondary")


def info_icon(label, text, icon=":material/info:", key=None):
    """자잘한 설명: 아이콘에 마우스를 올리면 툴팁으로, 누르면(휴대폰 포함) 작은 창으로 같은 내용을 보여 준다."""
    with st.popover(label, icon=icon, help=text, type="tertiary", key=key):
        st.markdown(text)


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


def toast(body, icon=None, long=False):
    """알림(st.toast). 오래 보이게(duration)는 그 인자가 있는 Streamlit 에서만 넘긴다 (공식 1.40 의 toast 에는 없음)."""
    kwargs = {"icon": icon}
    if "duration" in inspect.signature(st.toast).parameters:
        kwargs["duration"] = "long" if long else "short"
    st.toast(body, **kwargs)


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


TRYON_FILTERS = ["전체", "상의", "하의", "원피스", "아우터"]     # 가상 착용이 지원하는 종류 (core/tryon.py)
TRYON_COLUMNS = 6                                                # 고르기 창의 한 줄 카드 수
# 고르기 창: 카드 목록만 스크롤(끝에 닿아도 뒤 화면이 안 움직임), 고른 카드는 테두리·배경색으로도 표시한다.
TRYON_PICKER_CSS = """<style>
.st-key-tryon_grid { max-height: max(240px, calc(100vh - 430px)); overflow-y: auto; overscroll-behavior: contain;
  padding-right: 0.5rem; }
[class*="st-key-tryon_card_"]:has([data-testid="stIconMaterial"]) { border-color: #2F5BD3 !important; border-width: 2px;
  background: rgba(47, 91, 211, 0.08); }
[class*="st-key-tryon_card_"] button:has([data-testid="stIconMaterial"]) { background: #2F5BD3; border-color: #2F5BD3;
  color: #FFFFFF; }
</style>"""


def tryon_panel(app, items):
    """내 옷장 > 한번 입어보기: [입혀 볼 옷 고르기] 창에서 사진을 보며 최대 2벌을 고르고, 그 사진을 10분짜리
    서명 주소로 넘기며 Hugging Face ZeroGPU Space(가상 착용)를 띄운다."""
    supported = {x["id"]: x for x in tryon_supported(items)}
    st.caption("사람 사진에 옷을 입혀 보는 AI 예시 이미지예요. 실제 사이즈·핏은 반영되지 않아요. "
               "입힐 수 있는 옷은 상의·하의·원피스·아우터예요.")
    # 확정한 선택. 옷 목록이 바뀌면 새로 기본 선택(부위마다 한 벌)을 넣는다 (예전 선택이 남아 기본 선택이 안 들어가지 않게)
    confirm_key = f"tryon_pick_{hash(tuple(supported))}"
    if confirm_key not in st.session_state:
        st.session_state[confirm_key] = [x["id"] for x in tryon_default(list(supported.values()))]
    choices = tryon_order([supported[i] for i in st.session_state[confirm_key] if i in supported])
    _tryon_chosen(app, choices)
    if st.button("입혀 볼 옷 고르기", key="tryon_pick_open", disabled=not supported):
        st.session_state.tryon_draft = [x["id"] for x in choices]     # 창을 열 때마다 확정한 선택에서 시작
        st.session_state.tryon_q, st.session_state.tryon_cat = "", "전체"
        tryon_picker(app, supported, confirm_key)
    garments = [(app.media.signed_url(x.get("image_path"), seconds=600), tryon_kind(x.get("category"))) for x in choices]
    embed, page = space_addresses(app.tryon_url)
    if not supported:
        st.info("입힐 수 있는 옷(상의·하의·원피스·아우터)이 옷장에 없어요. 열린 화면에서 옷 사진을 직접 올려 주세요.")
    elif not choices:
        st.info("옷을 고르지 않으면 열린 화면에서 옷 사진을 직접 올려요.")
    elif any(g is None for g, _ in garments):
        st.info("사진이 없거나 넘길 수 없는 옷이 있어요 (스타터 옷장·로컬 실행 등). 열린 화면에서 옷 사진을 직접 올려 주세요.")
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.link_button("새 창(Hugging Face)에서 열기 · 권장", tryon_link(page, garments), type="primary")
        info_icon("사용량", "새 창에서는 내 무료 GPU 사용량으로 실행돼요 (하루 비로그인 약 2분, Hugging Face 무료 계정 로그인 시 "
                  "약 5분, 첫 사용 24시간 뒤 다시 채워짐). 이 화면 안에서 열면 바로 써 볼 수 있지만 Hugging Face 로그인이 "
                  "전달되지 않아 사용량이 더 빨리 바닥날 수 있어요. 무료 GPU라 대기열이 생길 수 있어요.",
                  icon=":material/timer:", key="tip_tryon_quota")
    # 탭은 안 보일 때도 함께 그려지므로, 켤 때만 Space 화면을 불러온다 (내 옷장을 열 때마다 Space 를 깨우지 않게)
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        inside = st.toggle("이 화면 안에서 열기", key="tryon_open")
        info_icon("사진 보관", "올린 사람 사진은 AI 옷장 DB에 저장하지 않아요. 옷 사진 주소는 10분 동안만 열려요.",
                  icon=":material/lock:", key="tip_tryon_privacy")
    if inside:
        if hasattr(st, "iframe"):      # 새 Streamlit (components.iframe 은 없어질 예정)
            st.iframe(tryon_link(embed, garments), height=820)
        else:
            import streamlit.components.v1 as components
            components.iframe(tryon_link(embed, garments), height=820, scrolling=True)


def _tryon_chosen(app, choices):
    """확정한 옷을 작은 사진과 함께 보여 준다 (입히는 순서대로)."""
    order = " → ".join(x["category"] for x in choices)
    st.markdown(f"**입혀 볼 옷** · {len(choices)}/{MAX_GARMENTS}벌" + (f" · {order} 순서로 입혀요" if choices else ""))
    if not choices:
        st.caption("아직 고른 옷이 없어요.")
        return
    cols = st.columns(6)
    for col, x in zip(cols, choices):
        with col:
            st.image(square_photo(app, x.get("image_path")), width="stretch",
                     alt=x["name"] if x.get("image_path") else f"{x['name']} (사진 없음)")
            st.caption(f"**{x['name']}**  \n{x['category']}")


def square_photo(app, ref):
    """고르기 화면용 정사각형 사진 (세션에 담아 두고 다시 만들지 않는다)."""
    cache = st.session_state.setdefault("thumb_cache", PhotoCache(15_000_000))
    key = f"square:{ref or ''}"
    if key not in cache:
        cache[key] = square_jpeg(app.media.read(ref) if ref else None)
    return cache[key]


def _tryon_match(item, query, cat):
    q = query.strip().lower()
    hay = f"{item['name']} {item.get('subcategory') or ''} {item['category']}".lower()
    return (cat in (None, "전체") or item["category"] == cat) and (not q or all(w in hay for w in q.split()))


def _tryon_toggle(item_id):
    draft = st.session_state.tryon_draft
    if item_id in draft:
        draft.remove(item_id)
    elif len(draft) < MAX_GARMENTS:
        draft.append(item_id)


@st.dialog("입혀 볼 옷 고르기", width="large")
def tryon_picker(app, supported, confirm_key):
    """사진 카드에서 최대 2벌을 고른다. [선택 완료]만 확정하고, [취소]·닫기는 확정한 선택을 그대로 둔다."""
    st.html(TRYON_PICKER_CSS)
    draft = st.session_state.tryon_draft
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    query = c1.text_input("옷 이름 또는 종류 검색", key="tryon_q", placeholder="예: 셔츠, 니트, 코트")
    cat = c2.segmented_control("종류", TRYON_FILTERS, key="tryon_cat")
    shown = [x for x in supported.values() if _tryon_match(x, query, cat)]
    full = len(draft) >= MAX_GARMENTS
    more = " · 아래로 내려 더 보기" if len(shown) > TRYON_COLUMNS else ""
    st.caption(f"입힐 수 있는 옷 {len(supported)}벌 중 {len(shown)}벌{more}")
    with st.container(key="tryon_grid"):
        if not shown:
            st.info("찾는 옷이 없어요. 다른 말로 검색하거나 종류를 '전체'로 바꿔 보세요.")
        for row in range(0, len(shown), TRYON_COLUMNS):
            for col, x in zip(st.columns(TRYON_COLUMNS), shown[row:row + TRYON_COLUMNS]):
                with col:
                    _tryon_card(app, x, x["id"] in draft, full)
    names = [x["name"] for x in tryon_order([supported[i] for i in draft])]
    st.markdown(f"**{len(draft)}/{MAX_GARMENTS}벌 선택**" + (" · " + ", ".join(names) if names else ""))
    if full:
        st.caption(f"최대 {MAX_GARMENTS}벌까지 고를 수 있어요. 바꾸려면 고른 옷을 먼저 해제하세요.")
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("취소", key="tryon_cancel"):
            st.rerun()
        if st.button("선택 완료", key="tryon_done", type="primary"):
            st.session_state[confirm_key] = list(draft)
            st.rerun()


def _tryon_card(app, item, selected, full):
    """사진 → [선택] → 이름·종류. 사진은 모두 정사각형이라 버튼이 한 줄로 맞는다 (이름 길이와 상관없이)."""
    with st.container(border=True, key=f"tryon_card_{item['id']}", gap="xsmall"):
        has_photo = bool(item.get("image_path"))
        st.image(square_photo(app, item.get("image_path")), width="stretch",
                 alt=item["name"] if has_photo else f"{item['name']} (사진 없음)")
        # 버튼 종류(primary/secondary)를 바꾸면 버튼이 새로 그려져 키보드 포커스를 잃는다 → 종류는 그대로 두고 문구·아이콘·색으로 표시
        st.button("선택됨" if selected else "선택", key=f"tryon_btn_{item['id']}", width="stretch",
                  icon=":material/check_circle:" if selected else None, disabled=full and not selected,
                  on_click=_tryon_toggle, args=(item["id"],))
        st.markdown(f"**{item['name']}**")
        detail = " · ".join(v for v in (item["category"], item.get("subcategory")) if v)
        st.caption(detail + ("" if has_photo else " · 사진 없음"))


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
