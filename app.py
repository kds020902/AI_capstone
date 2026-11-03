from datetime import date
from pathlib import Path
import hashlib
import io
import sqlite3

import pandas as pd
import streamlit as st
from PIL import ImageOps

from core import db
from core.batch_add import MAX_PHOTOS, after_remove, default_name, next_unconfirmed, step, take_room
from core.care import get_care_guide
from core.colors import COLOR_KO, normalize_color
from core.config import UnsafeKeyError, cloud_settings, get_setting
from core.media import LocalMedia, open_upright, to_cloud_jpeg
from core.recommender import recommend, explain_score_breakdown
from core.shopping import candidate_report
from core.taxonomy import (
    MAIN_CATEGORIES, NUM_SUBCATEGORIES, PURPOSES, RULES, SEASONS, SUBCATEGORIES, current_season, item_seasons,
    main_category_for_subcategory, manual_seasons, season_label, seasons_for,
)
from core.tryon import MAX_GARMENTS, space_addresses, tryon_default, tryon_kind, tryon_link, tryon_order, tryon_supported
from core.vision import SURE_CONFIDENCE, classify_clothing_image, dominant_color_name, model_status
from core.weather import CITY_PRESETS, RETURN_HOURS, coldest_until, get_current_weather

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "data" / "uploads"

APP_NAME = "AI 옷장"
st.set_page_config(page_title=APP_NAME, page_icon="👕", layout="wide")

for key, value in {
    "user_id": None,
    "active_user_id": None,
    "recommendations": None,
    "recommendation_session_id": None,
    "rec_meta": None,
    "weather_cache": None,
    "ba_cards": [],      # 직접 추가: 확인 중인 사진 카드 (최대 10장)
    "ba_idx": 0,
    "ba_seq": 0,
    "ba_nonce": 0,
    "ba_more": False,
    "ba_flash": [],
    "ba_names": set(),
}.items():
    st.session_state.setdefault(key, value)

# ------------------------------------------------------------------ 저장소: 로컬(SQLite) 또는 공개 배포(Supabase)
try:
    CLOUD = cloud_settings()
except UnsafeKeyError as e:
    st.error(str(e))
    st.stop()
TRYON_SPACE_URL = get_setting("TRYON_SPACE_URL")
WEB_TRIAL_URL = get_setting("WEB_TRIAL_URL")


def cloud_login_gate(settings):
    """로그인 전이면 로그인/가입 화면을 그리고 멈춘다. 로그인했으면 (store, media)."""
    from core import cloud_auth
    from core.cloud_store import CloudStore
    from core.media import SupabaseMedia

    if "sb_client" not in st.session_state:
        st.session_state.sb_client = cloud_auth.make_client(settings)
    client = st.session_state.sb_client
    user = cloud_auth.current_user(client)
    if user is None:
        st.title(f"👕 {APP_NAME}")
        st.caption("날씨·외출 목적·체질에 맞춰 내 옷장 속 옷으로 코디를 추천해요. 내 옷장은 로그인한 나만 볼 수 있어요.")
        tab_in, tab_up = st.tabs(["로그인", "회원가입"])
        with tab_in, st.form("login"):
            email = st.text_input("이메일", key="login_email")
            pw = st.text_input("비밀번호", type="password", key="login_pw")
            if st.form_submit_button("로그인", type="primary"):
                ok, msg = cloud_auth.sign_in(client, email.strip(), pw)
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()
        with tab_up, st.form("signup"):
            email2 = st.text_input("이메일", key="signup_email")
            pw2 = st.text_input("비밀번호 (6자 이상)", type="password", key="signup_pw")
            pw3 = st.text_input("비밀번호 확인", type="password", key="signup_pw2")
            if st.form_submit_button("가입하기", type="primary"):
                if pw2 != pw3:
                    st.error("비밀번호 확인이 달라요.")
                else:
                    ok, msg = cloud_auth.sign_up(client, email2.strip(), pw2)
                    (st.success if ok else st.info)(msg)
                    if ok:
                        st.rerun()
        st.caption("무료 체험 서비스예요 (비상업·학습·시연용). 사진과 기록은 내 계정에만 저장돼요.")
        st.stop()
    st.session_state.auth_email = user.email
    st.session_state.user_id = str(user.id)
    return CloudStore(client, user.id), SupabaseMedia(client, user.id)


if CLOUD:
    store, media = cloud_login_gate(CLOUD)
else:
    store, media = db, LocalMedia(UPLOAD_DIR)
    store.init_db()


def current_user():
    if not st.session_state.user_id:
        return None
    return store.get_user(st.session_state.user_id)


def show_image(ref, **kwargs):
    data = media.read(ref)
    if data:
        st.image(data, **kwargs)
        return True
    return False


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
def tryon_dialog(items):
    """Hugging Face ZeroGPU Space(가상 착용)를 이 창 안에 띄우고, 고른 옷 사진을 10분짜리 서명 주소로 넘긴다."""
    supported = tryon_supported(items)
    choices = tryon_default(supported)
    if len(supported) > 1:
        picked = st.multiselect("입혀 볼 옷 (최대 2벌, 상의 → 하의 순서로 입혀요)", list(range(len(supported))),
                                default=[supported.index(x) for x in choices], max_selections=MAX_GARMENTS,
                                format_func=lambda i: f"{supported[i]['name']} ({supported[i]['category']})")
        choices = tryon_order([supported[i] for i in picked])
    garments = [(media.signed_url(x.get("image_path"), seconds=600), tryon_kind(x.get("category"))) for x in choices]
    embed, page = space_addresses(TRYON_SPACE_URL)
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


def tryon_button(items, key):
    """가상 착용 Space 주소가 설정돼 있고, 입힐 수 있는 옷(상의·하의·원피스·아우터)이 있을 때만 버튼을 보인다."""
    if TRYON_SPACE_URL and tryon_supported(items):
        if st.button("👗 가상 착용(예시)", key=f"tryon_{key}"):
            tryon_dialog(items)


def gender_ko(g):
    return "여성" if g == "female" else "남성"


THERMAL_OPTIONS = ["추위를 잘 탐", "추위를 조금 탐", "보통", "더위를 조금 탐", "더위를 잘 탐"]
THERMAL_TO_SENSITIVITY = {
    "추위를 잘 탐": (1.0, 0.0),
    "추위를 조금 탐": (0.6, 0.1),
    "보통": (0.0, 0.0),
    "더위를 조금 탐": (0.1, 0.6),
    "더위를 잘 탐": (0.0, 1.0),
}
THERMAL_FEEDBACK = {None: "응답 안 함", -2: "매우 추움", -1: "약간 추움", 0: "적당함", 1: "약간 더움", 2: "매우 더움"}


@st.cache_data(show_spinner=False, max_entries=64)
def cached_candidate_report(cand, items, cold, heat):
    return candidate_report(cand, items, cold, heat)


def score_cell(cell, improved):
    if cell["best"] is None:
        return "–"
    return f"{cell['best'] * 100:.0f}점" + (" ▲" if improved else "")


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


# ------------------------------------------------------------------ 직접 추가: 사진 여러 장(최대 10장)을 카드로 확인
MATERIALS = ["", "cotton", "polyester", "denim", "wool", "linen", "nylon", "other"]
COLORS = list(COLOR_KO)
OTHER_KIND = "다른 종류"
# 사진·필드 부분만 따로 스크롤 (화면 맨 위에서 아래 버튼까지 한 화면에 들어오게, 최소 300px). 사진은 화면 높이에 맞춰 줄인다.
BA_CSS = """<style>
div:has(> [class*="st-key-ba_scroll_"]) {height: max(300px, calc(100dvh - 460px)) !important;
  flex: 0 0 max(300px, calc(100dvh - 460px)) !important;}
[class*="st-key-ba_scroll_"] * {word-break: keep-all;}
.st-key-ba_photo img {max-height: min(46vh, 440px); object-fit: contain;}
</style>"""


def _jpeg(img, side, square=False):
    small = ImageOps.fit(img, (side, side)) if square else img.copy()
    small.thumbnail((side, side))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def ba_key(card, field):
    return f"ba_{field}_{card['uid']}"


def ba_taken(card):
    """이 카드 말고 이미 쓰인 이름 (옷장 + 다른 카드). 자동 이름이 겹치지 않게."""
    ss = st.session_state
    return set(ss.ba_names) | {c["name"] for c in ss.ba_cards if c is not card}


def ba_new_card(uploaded, digest):
    """올린 사진 한 장 → 확인 카드 (AI 분류·대표색 추정까지). 열 수 없는 파일이면 None."""
    data, suffix = upload_bytes(uploaded)
    try:
        image = open_upright(io.BytesIO(data))
        if media.kind == "supabase":     # 어차피 긴 변 1280px JPEG로 저장하므로 미리 줄여 둔다 (무료 서버 메모리 절약)
            data, suffix = to_cloud_jpeg(data), ".jpg"
    except Exception:
        return None
    ai = classify_clothing_image(image)
    cands = [c for c in (ai.get("candidates") or []) if c["subcategory"] in RULES] if ai.get("ok") else []
    sub = cands[0]["subcategory"] if cands else SUBCATEGORIES[0]
    color = dominant_color_name(image)
    rule = RULES[sub]
    st.session_state.ba_seq += 1
    return {
        "uid": f"{st.session_state.ba_seq}_{digest[:8]}", "hash": digest, "data": data, "suffix": suffix,
        "view": _jpeg(image, 720), "thumb": _jpeg(image, 200, square=True), "ai": ai, "cands": cands,
        "pick": sub if cands else OTHER_KIND, "sub": sub, "cat": main_category_for_subcategory(sub),
        "color": color if color in COLOR_KO else COLORS[0], "warmth": rule["default_warmth"],
        "rain_ok": bool(rule["default_rain_ok"]), "material": "", "notes": "", "name": "", "name_auto": True,
        "confirmed": False,
    }


def ba_ingest(files):
    """새로 올린 사진을 카드로 만든다 (같은 사진은 한 번만, 모두 합쳐 10장까지)."""
    ss = st.session_state
    have = {c["hash"] for c in ss.ba_cards}
    fresh, dup = [], 0
    for f in files:
        digest = hashlib.md5(f.getvalue()).hexdigest()
        if digest in have:
            dup += 1
            continue
        have.add(digest)
        fresh.append((f, digest))
    fresh, dropped = take_room(len(ss.ba_cards), fresh)
    first_new, bad = len(ss.ba_cards), 0
    with st.spinner(f"AI가 사진 {len(fresh)}장을 분류하고 있어요…"):
        for f, digest in fresh:
            card = ba_new_card(f, digest)
            if card is None:
                bad += 1
                continue
            card["name"] = default_name(COLOR_KO[card["color"]], card["sub"], ba_taken(card))
            ss.ba_cards.append(card)
    if dropped:
        ss.ba_flash.append(("warning", f"한 번에 {MAX_PHOTOS}장까지라 {dropped}장은 넣지 않았어요. "
                                       "저장하거나 사진을 뺀 뒤 더 올려 주세요."))
    if dup:
        ss.ba_flash.append(("info", f"같은 사진 {dup}장은 한 번만 넣었어요."))
    if bad:
        ss.ba_flash.append(("warning", f"열 수 없는 파일 {bad}장은 넣지 않았어요."))
    if len(ss.ba_cards) > first_new:
        ss.ba_idx = first_new          # 새로 넣은 첫 사진부터 확인
    ss.ba_nonce += 1                   # 올리기 칸 비우기
    ss.ba_more = False


def ba_sync(card):
    """화면에서 고친 값을 카드에 옮긴다 (안 보이는 카드의 위젯 값은 Streamlit이 지우므로 카드에 따로 둔다).
    종류가 바뀌면 대분류·두께·비를 그 종류 기본값으로, 종류나 색이 바뀌면 직접 고치지 않은 이름도 다시 짓는다."""
    ss = st.session_state
    if ba_key(card, "pick") in ss:
        card["pick"] = ss[ba_key(card, "pick")]
    sub = card["pick"] if card["pick"] != OTHER_KIND else ss.get(ba_key(card, "other"), card["sub"])
    name = ss.get(ba_key(card, "name"), card["name"])
    if name != card["name"]:
        card["name"] = name.strip()
        card["name_auto"] = not card["name"]        # 이름을 비우면 다시 자동 이름
    for f, field in (("cat", "cat"), ("warmth", "warmth"), ("rain", "rain_ok"), ("material", "material"),
                     ("notes", "notes")):
        if ba_key(card, f) in ss:
            card[field] = ss[ba_key(card, f)]
    color = ss.get(ba_key(card, "color"), card["color"])
    changed = sub != card["sub"] or color != card["color"]
    if sub != card["sub"]:
        rule = RULES[sub]
        card.update(sub=sub, cat=main_category_for_subcategory(sub), warmth=rule["default_warmth"],
                    rain_ok=bool(rule["default_rain_ok"]))
    card["color"] = color
    if card["name_auto"] and (changed or not card["name"]):
        card["name"] = default_name(COLOR_KO[color], sub, ba_taken(card))


def ba_seed(card):
    """카드 값을 위젯에 채운다 (위젯을 그리기 전에)."""
    ss = st.session_state
    values = {"pick": card["pick"], "name": card["name"], "color": card["color"], "cat": card["cat"],
              "warmth": card["warmth"], "rain": card["rain_ok"], "material": card["material"], "notes": card["notes"]}
    if card["pick"] == OTHER_KIND:
        values["other"] = card["sub"]
    for f, v in values.items():
        ss[ba_key(card, f)] = v


def ba_go(delta=0, to=None):
    ss = st.session_state
    if not ss.ba_cards:
        return
    ba_sync(ss.ba_cards[ss.ba_idx])
    ss.ba_idx = to if to is not None else step(ss.ba_idx, len(ss.ba_cards), delta)


def ba_confirm_next():
    ss = st.session_state
    card = ss.ba_cards[ss.ba_idx]
    ba_sync(card)
    card["confirmed"] = True
    nxt = next_unconfirmed([c["confirmed"] for c in ss.ba_cards], ss.ba_idx)
    if nxt is None:
        ss.ba_flash.append(("success", f"{len(ss.ba_cards)}장을 모두 확인했어요. 아래 '저장'을 누르면 옷장에 넣어요."))
    else:
        ss.ba_idx = nxt


def ba_remove():
    ss = st.session_state
    ss.ba_cards.pop(ss.ba_idx)
    ss.ba_idx = after_remove(ss.ba_idx, len(ss.ba_cards))
    if not ss.ba_cards:
        ss.ba_more = False


def ba_clear():
    ss = st.session_state
    ss.ba_cards, ss.ba_idx, ss.ba_more = [], 0, False
    ss.ba_nonce += 1


@st.dialog("사진 목록", width="large")
def ba_list_dialog():
    ss = st.session_state
    cards = ss.ba_cards
    st.caption(f"확인 {sum(c['confirmed'] for c in cards)} / {len(cards)} · 번호를 누르면 그 사진으로 가요.")
    with st.container(horizontal=True, wrap=True, gap="small"):
        for i, c in enumerate(cards):
            with st.container(width=88):      # 휴대폰에서도 한 줄에 3장
                st.image(c["thumb"], width="stretch")
                st.caption(("✓ " if c["confirmed"] else "") + c["sub"])
                if st.button(f"{i + 1}번", key=f"ba_go_{i}", width="stretch",
                             type="primary" if i == ss.ba_idx else "secondary"):
                    ba_go(to=i)
                    st.rerun()


def ba_fields(card):
    """카드 오른쪽(휴대폰은 아래): AI 후보 → 이름·색 → 대분류·두께 → 설명 → 더 보기."""
    ai, k = card["ai"], (lambda f: ba_key(card, f))
    if card["cands"]:
        best = card["cands"][0]
        if ai.get("sure"):
            st.success(f"AI 판단: {best['subcategory']} ({best['confidence'] * 100:.0f}%). 다르면 다른 후보를 골라 주세요.")
        else:
            st.warning(f"AI가 확실하지 않아요 (1등 {best['confidence'] * 100:.0f}%). 맞는 종류를 골라 주세요.")
        conf = {c["subcategory"]: c["confidence"] for c in card["cands"]}
        st.radio("종류 (AI 후보)", list(conf) + [OTHER_KIND], horizontal=True, key=k("pick"),
                 format_func=lambda s: f"{s} {conf[s] * 100:.0f}%" if s in conf else s)
        if card["pick"] == OTHER_KIND:
            st.selectbox(f"다른 종류 ({NUM_SUBCATEGORIES}종)", SUBCATEGORIES, key=k("other"))
    else:
        if not ai.get("ok"):
            st.caption(ai.get("message", ""))
        st.selectbox(f"종류 ({NUM_SUBCATEGORIES}종)", SUBCATEGORIES, key=k("other"))
    c1, c2 = st.columns(2)
    c1.text_input("이름", key=k("name"))
    c2.selectbox("색 (자동 추정)", COLORS, key=k("color"), format_func=lambda c: COLOR_KO[c])
    c3, c4 = st.columns(2)
    c3.selectbox("대분류", MAIN_CATEGORIES, key=k("cat"))
    c4.slider("두께/보온", 1, 5, key=k("warmth"))
    st.text_input("설명 (선택)", key=k("notes"), placeholder="예: 기모 안감, 오버핏")
    with st.expander("더 보기 · 소재, 비 오는 날"):
        st.selectbox("소재", MATERIALS, key=k("material"), format_func=lambda m: m or "모름")
        st.checkbox("비 오는 날 부담이 적음", key=k("rain"))


def ba_save(user, cards):
    """카드들을 옷장에 한 번에 넣는다. 사진은 한 장씩 저장소에, 옷 정보는 한 번에 DB에."""
    rows = []
    for i, c in enumerate(cards):
        path = media.save(user["id"], f"b{i}_", c["data"], c["suffix"])    # 같은 순간에 저장해도 이름이 겹치지 않게
        ai = c["ai"] if c["ai"].get("ok") else {}                          # 기록용: AI 1등 답
        rows.append({"name": c["name"], "category": c["cat"], "subcategory": c["sub"], "color": c["color"],
                     "warmth": c["warmth"], "rain_ok": c["rain_ok"], "material": c["material"], "image_path": path,
                     "notes": c["notes"].strip(), "ai_category": ai.get("category"),
                     "ai_subcategory": ai.get("subcategory"), "ai_confidence": ai.get("confidence")})
    store.create_wardrobe_items(user["id"], rows)


def render_direct_add(user):
    """내 옷장 > 직접 추가. 사진을 올리면 AI가 채운 카드를 한 장씩 확인하고 저장한다 (1장이어도 같은 화면)."""
    ss = st.session_state
    st.html(BA_CSS)
    ss.ba_names = {x["name"] for x in store.list_wardrobe_items(user["id"], active_only=False)}
    for kind, text in ss.ba_flash:      # 알림은 떠 있다 사라지게 (카드가 아래로 밀리지 않게)
        st.toast(text, icon={"success": "✅", "info": "ℹ️"}.get(kind, "⚠️"),
                 duration="long" if kind == "warning" else "short")
    ss.ba_flash = []
    cards = ss.ba_cards

    if not cards or ss.ba_more:
        if len(cards) < MAX_PHOTOS:
            label = ("저장할 의류를 넣어 주세요 (최대 10장까지)" if not cards
                     else f"사진 더 넣기 (지금 {len(cards)}장 · 최대 {MAX_PHOTOS}장까지)")
            files = st.file_uploader(label, type=["jpg", "jpeg", "png"], accept_multiple_files=True,
                                     key=f"ba_up_{ss.ba_nonce}")
            if files:
                ba_ingest(files)
                st.rerun()
        else:
            st.info(f"이미 {MAX_PHOTOS}장이에요. 사진을 빼거나 저장하면 더 넣을 수 있어요.")
        if not cards:
            st.caption("올리면 AI가 종류·색·이름을 먼저 채워 줘요. 한 장씩 확인하고 저장하세요.")
            return
        with st.container(horizontal=True):
            if st.button("닫기", key="ba_more_close"):
                ss.ba_more = False
                st.rerun()
            st.button("지금 사진 모두 빼고 처음부터", key="ba_clear", type="tertiary", on_click=ba_clear)

    n = len(cards)
    ss.ba_idx = min(ss.ba_idx, n - 1)
    idx, card = ss.ba_idx, cards[ss.ba_idx]
    done = sum(c["confirmed"] for c in cards)
    multi = n > 1
    ai_note = "AI 분류 완료" if all(c["cands"] for c in cards) else "AI 분류를 못 한 사진은 종류를 직접 골라 주세요"
    with st.container(horizontal=True, vertical_alignment="bottom"):
        if multi:
            st.progress(done / n, text=f"📎 사진 {n}장 · 확인 {done} / {n}")
        else:
            st.caption(f"📎 사진 1장 · {ai_note}")
        if st.button("목록", icon=":material/grid_view:", key="ba_list", disabled=not multi):
            ba_list_dialog()

    ba_sync(card)
    ba_seed(card)
    with st.container(height=480, border=True, key=f"ba_scroll_{card['uid']}"):
        c_photo, c_form = st.columns([1, 1.2], gap="medium")
        with c_photo:
            with st.container(horizontal=True, vertical_alignment="center", key="ba_photo"):
                st.button("‹", key="ba_prev", on_click=ba_go, args=(-1,), disabled=not multi, help="이전 사진")
                st.image(card["view"], width="stretch")
                st.button("›", key="ba_next", on_click=ba_go, args=(1,), disabled=not multi, help="다음 사진")
            line = f":gray-badge[{idx + 1} / {n}]"
            if multi:
                line += " " + (":green-badge[✓ 확인함]" if card["confirmed"] else ":orange-badge[확인 전]") + " &nbsp; "
                line += " ".join(":red[◉]" if i == idx else (":green[●]" if c["confirmed"] else ":gray[●]")
                                 for i, c in enumerate(cards))
            st.markdown(line)
        with c_form:
            ba_fields(card)

    all_done = done == n
    if multi:
        st.button("✓ 모두 확인했어요" if all_done else "✓ 확인하고 다음 ›", key="ba_confirm", type="primary",
                  width="stretch", on_click=ba_confirm_next, disabled=all_done)
    to_save = [c for c in cards if c["confirmed"]] if multi else cards
    with st.container(horizontal=True):
        st.button("사진 빼기", key="ba_remove", on_click=ba_remove, width="stretch")
        if st.button("다시 올리기", key="ba_more_open", width="stretch"):
            ss.ba_more = True
            st.rerun()
        save = st.button(f"저장 ({len(to_save)}장)" if multi else "저장", key="ba_save", width="stretch",
                         type="primary" if (all_done or not multi) else "secondary", disabled=not to_save)
    if multi and not all_done:
        st.caption("'확인하고 다음'을 누른 사진만 저장해요. 목록에서 원하는 사진으로 바로 갈 수 있어요.")
    if save:
        try:
            ba_save(user, to_save)
        except Exception as e:
            st.error(f"저장하지 못했어요 ({type(e).__name__}). 잠시 뒤 다시 눌러 주세요.")
            return
        saved = {id(c) for c in to_save}
        ss.ba_cards = [c for c in cards if id(c) not in saved]
        ss.ba_idx = 0
        ss.ba_flash.append(("success", f"{len(to_save)}벌을 옷장에 넣었어요. '옷장 목록'에서 볼 수 있어요."
                            + (f" 남은 사진 {len(ss.ba_cards)}장도 확인해 주세요." if ss.ba_cards else "")))
        st.rerun()


@st.dialog("정보 수정")
def edit_item_dialog(item, user_id):
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
        store.update_wardrobe_item(iid, user_id, name.strip(), cat, sub, color, warmth, rain, material, notes.strip())
        st.rerun()


# ------------------------------------------------------------------ 사이드바
st.sidebar.title(f"👕 {APP_NAME}")
if CLOUD:
    st.sidebar.caption(f"로그인: {st.session_state.get('auth_email') or '-'}")
    if st.sidebar.button("로그아웃"):
        from core import cloud_auth
        cloud_auth.sign_out(st.session_state.sb_client)
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()
    if not current_user():
        st.sidebar.info("프로필에서 내 정보를 먼저 저장하세요.")
else:
    users = store.list_users()
    if users:
        options = {f"{u['name']} · {gender_ko(u['gender'])} (ID {u['id']})": u["id"] for u in users}
        labels = list(options)
        idx = next((i for i, lb in enumerate(labels) if options[lb] == st.session_state.user_id), 0)
        selected = st.sidebar.selectbox("사용자", labels, index=idx)
        st.session_state.user_id = options[selected]
    else:
        st.sidebar.info("프로필에서 사용자를 먼저 생성하세요.")

# 사용자를 바꾸면 이전 사용자의 추천·AI 분류 결과를 비운다 (안 비우면 다른 사람의 추천이 저장될 수 있음)
if st.session_state.active_user_id != st.session_state.user_id:
    st.session_state.active_user_id = st.session_state.user_id
    st.session_state.recommendations = None
    st.session_state.recommendation_session_id = None
    st.session_state.rec_meta = None
    st.session_state.ba_cards = []
    st.session_state.ba_idx = 0
    st.session_state.ba_more = False
    st.session_state.ba_flash = []

page = st.sidebar.radio("메뉴", ["홈", "프로필", "내 옷장", "오늘의 추천", "사기 전에 맞춰보기", "저장 코디", "KPI·데이터"])
st.sidebar.divider()
status = model_status()
st.sidebar.caption(f"의류 분류: {status['classifier']}")
if WEB_TRIAL_URL:
    st.sidebar.link_button("설치 없는 웹 체험판", WEB_TRIAL_URL)

# ------------------------------------------------------------------ 홈
if page == "홈":
    st.title(APP_NAME)
    st.subheader(f"성별 프로필 + 30개 캐주얼 스타터 옷장 + {NUM_SUBCATEGORIES}종 세부분류")
    user = current_user()
    if user:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("프로필", f"{user['name']} · {gender_ko(user['gender'])}")
        c2.metric("등록 의류", store.count_wardrobe_items(user["id"]))
        c3.metric("추천 세션", store.count_recommendation_sessions(user["id"]))
        acc = store.top3_acceptance_rate(user["id"])
        c4.metric("Top 3 수용", "-" if acc is None else f"{acc*100:.1f}%")
        st.info(
            "스타터 옷장은 실제 소유 여부를 대신 확정하는 기능이 아닙니다. "
            "처음 테스트할 때 30개를 한 번에 불러오고, 없는 옷은 비활성화/삭제해서 사용하세요."
        )
    else:
        st.warning("프로필을 생성하세요.")
    if CLOUD:
        with st.expander("무료 체험 서비스 안내"):
            st.markdown(
                "- 비상업·학습·시연용 캡스톤 프로젝트예요. 광고·결제가 없어요.\n"
                "- 무료 서버라 한동안 아무도 안 쓰면 잠들어요. 처음 접속이 느리면 깨우는 중이에요.\n"
                "- 날씨는 Open-Meteo 무료 API를 써요. 불러오기 실패 시 '수동 입력'으로 추천받을 수 있어요.\n"
                "- 가상 착용은 무료 GPU를 여러 사람이 나눠 쓰는 방식이라 대기와 하루 사용량 제한이 있어요."
            )

# ------------------------------------------------------------------ 프로필
elif page == "프로필":
    st.title("프로필")
    if not CLOUD or current_user() is None:
        with st.form("create_user"):
            st.subheader("내 정보" if CLOUD else "새 사용자")
            name = st.text_input("이름 / 닉네임")
            gender_label = st.radio("성별", ["남성", "여성"], horizontal=True)
            gender = "male" if gender_label == "남성" else "female"
            thermal = st.radio("체감 성향", THERMAL_OPTIONS, index=2, horizontal=True)
            cold, heat = THERMAL_TO_SENSITIVITY[thermal]

            if st.form_submit_button("저장" if CLOUD else "사용자 생성", type="primary"):
                if not name.strip():
                    st.error("이름을 입력하세요.")
                else:
                    try:
                        uid = store.create_user(name.strip(), gender, cold, heat)
                    except sqlite3.IntegrityError:
                        st.error(f"'{name.strip()}'은(는) 이미 있는 이름입니다. 다른 이름을 입력하세요.")
                    else:
                        st.session_state.user_id = uid
                        st.success("저장했습니다." if CLOUD else "사용자를 생성했습니다.")
                        st.rerun()

    user = current_user()
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
                store.update_user(user["id"], gender2, cold2, heat2)
                st.success("저장했습니다.")
                st.rerun()

# ------------------------------------------------------------------ 내 옷장
elif page == "내 옷장":
    st.title("내 옷장")
    user = current_user()
    if not user:
        st.warning("사용자를 생성하세요.")
        st.stop()

    st.caption(f"현재 프로필: {user['name']} · {gender_ko(user['gender'])}")
    tab1, tab2, tab3 = st.tabs(["옷장 목록", "30개 스타터 옷장", "직접 추가"])

    with tab1:
        items = store.list_wardrobe_items(user["id"], active_only=True)
        season_now = current_season(date.today().month)
        with st.expander(f"계절 정리 · 지금은 {season_now}"):
            season = st.radio("계절", SEASONS, index=SEASONS.index(season_now), horizontal=True, key="tidy_season")
            off = [x for x in items if season not in item_seasons(x)]
            back = [x for x in store.list_inactive_items(user["id"]) if season in item_seasons(x)]
            c1, c2 = st.columns(2)
            if c1.button(f"{season}에 안 입는 옷 {len(off)}벌 넣어두기", disabled=not off):
                store.set_items_active([x["id"] for x in off], user["id"], False)
                st.rerun()
            if c2.button(f"넣어 둔 옷 중 {season} 옷 {len(back)}벌 꺼내기", disabled=not back):
                store.set_items_active([x["id"] for x in back], user["id"], True)
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
                        show_image(item["image_path"])
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
                        tryon_button([item], f"item_{item['id']}")
                        with st.container(horizontal=True):
                            if st.button("정보 수정", key=f"edit_{item['id']}"):
                                edit_item_dialog(item, user["id"])
                            with st.popover("계절 수정"):
                                picked = st.multiselect("입는 계절", SEASONS, default=item_seasons(item),
                                                        key=f"season_{item['id']}")
                                if st.button("저장", key=f"season_save_{item['id']}"):
                                    same_as_auto = picked == seasons_for(item.get("subcategory"))
                                    store.update_item_seasons(item["id"], user["id"], None if same_as_auto else picked)
                                    st.rerun()
                                st.caption(f"자동: {season_label(seasons_for(item.get('subcategory')))} (옷 종류 기준). "
                                           "전부 지우고 저장하면 자동으로 돌아가요.")
                        c_a, c_b = st.columns(2)
                        if c_a.button("비활성화", key=f"off_{item['id']}"):
                            store.deactivate_item(item["id"], user["id"])
                            st.rerun()
                        if c_b.button("삭제", key=f"del_{item['id']}"):
                            store.soft_delete_item(item["id"], user["id"])
                            st.rerun()

        # 비활성화한 옷 다시 활성화
        inactive = store.list_inactive_items(user["id"])
        with st.expander(f"넣어 둔 옷 · 비활성화 ({len(inactive)}개)"):
            if not inactive:
                st.caption("넣어 둔 옷이 없습니다.")
            for item in inactive:
                c_a, c_b = st.columns([4, 1])
                c_a.write(f"{item['name']} · {item['category']} / {item.get('subcategory') or '-'} · "
                          f"{season_label(item_seasons(item))}")
                if c_b.button("다시 활성화", key=f"on_{item['id']}"):
                    store.reactivate_item(item["id"], user["id"])
                    st.rerun()

    with tab2:
        st.subheader(f"{gender_ko(user['gender'])} 캐주얼 스타터 옷장 30개")
        st.write(
            "일일이 옷을 등록하지 않고 테스트를 시작할 수 있도록 자주 쓰이는 캐주얼 아이템 30개를 넣습니다. "
            "실제로 보유하지 않은 항목은 목록에서 비활성화/삭제하세요."
        )
        if st.button("프로필 성별 기준 30개 추가", type="primary"):
            added = store.add_starter_wardrobe(user["id"])
            st.success(f"{added}개를 추가했습니다. 이미 있던 이름은 중복 추가하지 않았습니다.")
            st.rerun()

    with tab3:
        render_direct_add(user)

# ------------------------------------------------------------------ 오늘의 추천
elif page == "오늘의 추천":
    st.title("오늘의 추천")
    user = current_user()
    if not user:
        st.warning("사용자를 생성하세요.")
        st.stop()
    items = store.list_wardrobe_items(user["id"], active_only=True)
    cats = {x["category"] for x in items}
    if not (("상의" in cats and "하의" in cats and "신발" in cats) or ("원피스" in cats and "신발" in cats)):
        st.warning("상의+하의+신발 또는 원피스+신발 조합이 필요합니다.")
        st.stop()

    mode = st.radio("날씨", ["자동 조회", "수동 입력"], horizontal=True)
    temperature, humidity, apparent, precipitation, rain, wind, source = 22.0, 70.0, 22.0, 0.0, False, 8.0, "manual"
    later = None  # 귀가 전 가장 추운 때 → 추천마다 '챙길 겉옷' 안내

    if mode == "자동 조회":
        c1, c2 = st.columns(2)
        city = c1.selectbox("도시", list(CITY_PRESETS))
        return_hour = c2.select_slider("귀가 예정", RETURN_HOURS, value=22,
                                       format_func=lambda h: "자정" if h == 24 else f"{h}시")
        lat, lon = CITY_PRESETS[city]
        if st.button("현재 날씨 불러오기"):
            st.session_state.weather_cache = get_current_weather(lat, lon)
        data = st.session_state.weather_cache
        if data and data.get("ok"):
            temperature = data["temperature"]
            humidity = data["humidity"]
            apparent = data["apparent_temperature"]
            precipitation = data["precipitation"]
            rain = data["rain"] > 0 or precipitation > 0
            wind = data["wind_speed"]
            source = data["source"]
            later = coldest_until(data, return_hour)
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("기온", f"{temperature:.1f}℃")
            c2.metric("체감", f"{apparent:.1f}℃")
            c3.metric("습도", f"{humidity:.0f}%")
            c4.metric("풍속", f"{wind:.1f} km/h")
            if later:
                c5.metric(f"귀가 전 최저 체감 ({later['label']})", f"{later['apparent_temperature']:.1f}℃")
        elif data:
            st.warning(data["message"])
        else:
            st.caption("날씨를 불러오기 전에는 기본값(22℃, 비 없음)으로 추천합니다.")
    else:
        c1, c2, c3 = st.columns(3)
        temperature = c1.slider("기온", -10.0, 40.0, 22.0, 0.5)
        humidity = c2.slider("습도", 10.0, 100.0, 70.0, 1.0)
        apparent = c3.slider("체감온도", -15.0, 45.0, 22.0, 0.5)
        c4, c5, c6 = st.columns(3)
        rain = c4.checkbox("비가 옴")
        precipitation = c5.number_input("강수량(mm)", 0.0, 100.0, 0.0, 0.5)
        wind = c6.slider("풍속(km/h)", 0.0, 60.0, 8.0, 0.5)
        c7, c8 = st.columns(2)
        if c7.checkbox("저녁 늦게까지 밖에 있어요"):
            later_app = c8.slider("저녁 체감온도", -15.0, 45.0, max(-15.0, apparent - 6), 0.5)
            later = {"temperature": later_app, "apparent_temperature": later_app, "label": "저녁"}

    purpose = st.selectbox("외출 목적", ["등교", "데이트", "운동", "격식"])
    cold = float(user["cold_sensitivity"])
    heat = float(user["heat_sensitivity"])

    if st.button("TOP 3 추천 생성", type="primary", use_container_width=True):
        out = recommend(
            items, temperature, apparent, humidity, precipitation, rain, wind,
            purpose, cold, heat, 3,
            exclude_keys=store.disliked_item_sets(user["id"]),
            seed=f"{user['id']}-{date.today().isoformat()}",  # 같은 날엔 같은 결과, 날마다 동점 코디가 바뀜
            later=later,
        )
        sid = store.create_recommendation_session(
            user["id"], purpose, temperature, apparent,
            humidity, precipitation, rain, wind, source
        )
        for rank, result in enumerate(out["results"], 1):
            result["recommendation_id"] = store.save_recommendation(sid, rank, result)
        st.session_state.recommendations = out["results"]
        st.session_state.recommendation_session_id = sid
        st.session_state.rec_meta = {"warnings": out["warnings"], "context": out["context"]}

    results = st.session_state.recommendations
    meta = st.session_state.rec_meta or {}
    if meta.get("context"):
        ctx = meta["context"]
        adj = ", ".join(f"{label} {delta:+.0f}℃" for label, delta in ctx["adjustments"]) or "보정 없음"
        st.caption(f"유효 기온: 체감 {ctx['base_temp']:.1f}℃ → ({adj}) → **{ctx['eff']:.1f}℃** 기준으로 추천")
        if ctx.get("later"):
            lt = ctx["later"]
            st.caption(f"{lt['label']}: 체감 {lt['base_temp']:.1f}℃ → 유효 **{lt['eff']:.1f}℃** "
                       f"(지금보다 {ctx['eff'] - lt['eff']:.1f}℃ 낮음)")
    for w in meta.get("warnings", []):
        st.warning(w)
    if results == []:
        st.error("추천할 수 있는 코디가 없습니다.")
    if results:
        for rank, result in enumerate(results, 1):
            rid = result["recommendation_id"]
            with st.container(border=True):
                st.markdown(f"## {rank}위 · {result['score']*100:.1f}%")
                st.write(" + ".join(p["name"] for p in result["pieces"]))
                if result.get("carry"):
                    st.markdown(f"🧥 **저녁 대비 겉옷:** {result['carry']['name']}")
                for reason in result["reasons"]:
                    st.write(f"- {reason}")
                with st.expander("점수 근거"):
                    st.dataframe(pd.DataFrame(explain_score_breakdown(result["components"], purpose)),
                                 hide_index=True, use_container_width=True)
                tryon_button(result["pieces"], f"rec_{rid}")
                c1, c2, c3 = st.columns(3)
                if c1.button("이 코디 저장", key=f"s_{rid}"):
                    store.save_outfit(user["id"], rid)
                    store.complete_recommendation_session(st.session_state.recommendation_session_id)
                    st.success("코디를 저장했습니다.")
                if c2.button("👍 좋아요", key=f"l_{rid}"):
                    store.upsert_feedback(user["id"], rid, rating="like")
                    st.toast("좋아요를 기록했습니다.")
                if c3.button("👎 별로", key=f"d_{rid}"):
                    store.upsert_feedback(user["id"], rid, rating="dislike")
                    st.toast("다음 추천부터 이 조합은 제외합니다.")

                with st.expander("착용 후 피드백"):
                    thermal = st.radio(
                        "체감", list(THERMAL_FEEDBACK), index=0, horizontal=True,
                        format_func=lambda x: THERMAL_FEEDBACK[x], key=f"t_{rid}",
                    )
                    rating = st.slider("코디 만족도", 1, 5, 3, key=f"r_{rid}")
                    worn = st.checkbox("실제로 착용함", key=f"w_{rid}")
                    if st.button("피드백 저장", key=f"f_{rid}"):
                        store.upsert_feedback(user["id"], rid, worn=worn, thermal_feedback=thermal, satisfaction=rating)
                        st.success("저장했습니다.")

# ------------------------------------------------------------------ 사기 전에 맞춰보기
elif page == "사기 전에 맞춰보기":
    st.title("사기 전에 맞춰보기")
    st.caption("쇼핑몰 상품 사진이나 화면 캡처를 올리면 지금 옷장과 맞춰 봐요. 옷장에는 넣지 않고 '구매 후보'로 따로 둬요.")
    user = current_user()
    if not user:
        st.stop()
    items = store.list_wardrobe_items(user["id"], active_only=True)
    candidates = store.list_candidates(user["id"])

    with st.expander("구매 후보 추가", expanded=not candidates):
        up = st.file_uploader("상품 사진 또는 캡처 (JPG/PNG)", type=["jpg", "jpeg", "png"], key="cand_upload")
        cand_ai, cand_color, cand_pick = None, "", None
        if up:
            cand_image = open_upright(up)
            st.image(cand_image, width=220)
            cand_color = dominant_color_name(cand_image) or ""
            ai_key = f"cand_ai_{up.file_id}"  # 다시 그릴 때마다 분류하지 않도록
            if ai_key not in st.session_state:
                st.session_state[ai_key] = classify_clothing_image(cand_image)
            cand_ai = st.session_state[ai_key]
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
                path = media.save(user["id"], "cand_", data, suffix)
            store.create_candidate(user["id"], cand_name.strip(), main_category_for_subcategory(cand_sub), cand_sub,
                                cand_color, cand_warmth, cand_rain, image_path=path, link=cand_link.strip(),
                                ai_subcategory=ai_sub, ai_confidence=cand_ai.get("confidence") if cand_ai else None)
            st.rerun()

    if not candidates:
        st.info("아직 구매 후보가 없어요. 위에서 상품 사진을 올려 보세요.")
        st.stop()
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
                show_image(cand["image_path"])
                tryon_button([cand], f"cand_{cand['id']}")
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
            st.dataframe(table, use_container_width=True)
            cell = rep["cells"][(view_season, view_purpose)]
            if cell["results"]:
                st.markdown(f"**{view_season} · {view_purpose} 코디 (체감 {cell['temp']}℃ 기준)**")
                for r in cell["results"]:
                    st.write(f"- {r['score'] * 100:.0f}점 · " + " + ".join(p["name"] for p in r["pieces"]))
            else:
                st.caption(f"{view_season} · {view_purpose}에는 이 옷으로 만들 코디가 없어요.")
            c_a, c_b = st.columns(2)
            if c_a.button("샀어요 · 옷장에 넣기", key=f"buy_{cand['id']}"):
                store.buy_candidate(cand["id"], user["id"])
                st.toast(f"{cand['name']}을(를) 옷장에 넣었어요.")
                st.rerun()
            if c_b.button("후보에서 지우기", key=f"cdel_{cand['id']}"):
                store.delete_candidate(cand["id"], user["id"])
                st.rerun()

# ------------------------------------------------------------------ 저장 코디
elif page == "저장 코디":
    st.title("저장 코디")
    user = current_user()
    if not user:
        st.stop()
    saved = store.list_saved_outfits(user["id"])
    if not saved:
        st.info("저장된 코디가 없습니다.")
    for row in saved:
        with st.container(border=True):
            st.markdown(f"### {row['saved_at'][:16]} · 추천 {row['rank']}위")
            st.write(row["outfit_text"])
            st.caption(f"{row['purpose']} · {row['temperature']:.1f}℃ · 습도 {row['humidity']:.0f}% · 점수 {row['score']*100:.1f}%")

# ------------------------------------------------------------------ KPI
elif page == "KPI·데이터":
    st.title("KPI · 데이터")
    user = current_user()
    if not user:
        st.stop()
    stats = store.user_kpi_stats(user["id"])
    c1, c2 = st.columns(2)
    c1.metric("추천 세션", stats["completed_sessions"])
    c2.metric("Top 3 수용률", "-" if stats["top3_acceptance_rate"] is None else f"{stats['top3_acceptance_rate']*100:.1f}%")

    logs = store.export_user_logs(user["id"])
    if not logs.empty:
        st.dataframe(logs, use_container_width=True, hide_index=True)
        st.download_button("추천 로그 CSV", logs.to_csv(index=False).encode("utf-8-sig"),
                           f"user_{user['id']}_logs.csv", "text/csv")
