"""내 옷장 > 직접 추가: 사진 여러 장(최대 10장)을 올리면 AI가 채운 카드를 한 장씩 확인하고 한 번에 저장한다."""
from __future__ import annotations

import hashlib
import io
import uuid

import streamlit as st
from PIL import ImageOps

from core.batch_add import MAX_PHOTOS, after_remove, default_name, next_unconfirmed, step, take_room
from core.colors import COLOR_KO
from core.media import open_upright, to_cloud_jpeg
from core.taxonomy import MAIN_CATEGORIES, NUM_SUBCATEGORIES, RULES, SUBCATEGORIES, main_category_for_subcategory
from core.vision import classify_clothing_image, dominant_color_name
from screens.common import COLORS, MATERIALS, upload_bytes

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


def ba_new_card(app, uploaded, digest):
    """올린 사진 한 장 → 확인 카드 (AI 분류·대표색 추정까지). 열 수 없는 파일이면 None."""
    data, suffix = upload_bytes(uploaded)
    try:
        image = open_upright(io.BytesIO(data))
        ready = app.media.kind == "supabase"  # 어차피 긴 변 1280px JPEG로 저장하므로 미리 줄여 둔다 (무료 서버 메모리 절약)
        if ready:                             # AI 분류는 위에서 연 원본 사진으로 한다
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
        "req": str(uuid.uuid4()), "path": "",        # 등록 요청 id (사진 이름·DB 중복 방지), 올린 사진 경로
        "ready": ready,                              # 이미 저장용 JPEG로 줄였으면 저장할 때 다시 압축하지 않음
        "view": _jpeg(image, 720), "thumb": _jpeg(image, 200, square=True), "ai": ai, "cands": cands,
        "pick": sub if cands else OTHER_KIND, "sub": sub, "cat": main_category_for_subcategory(sub),
        "color": color if color in COLOR_KO else COLORS[0], "warmth": rule["default_warmth"],
        "rain_ok": bool(rule["default_rain_ok"]), "material": "", "notes": "", "name": "", "name_auto": True,
        "confirmed": False,
    }


def ba_ingest(app, files):
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
            card = ba_new_card(app, f, digest)
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


def ba_save(app, user, cards):
    """카드들을 옷장에 한 번에 넣는다. 사진은 한 장씩 저장소에, 옷 정보는 한 번에 DB에.
    카드마다 고정된 요청 id 를 쓴다: 중간에 실패해 다시 눌러도 이미 올린 사진은 그 경로를 다시 쓰고(사진 이름도 요청 id라
    응답만 못 받은 사진은 같은 파일에 덮어씀), 이미 DB에 들어간 옷은 다시 넣지 않는다. 실패한 저장의 사진을 지우지 않는
    이유: 응답만 유실돼 실제로는 DB에 연결됐을 수 있다. 남는 사진은 버린 카드당 1장을 넘지 않는다."""
    rows = []
    for c in cards:
        c.setdefault("req", str(uuid.uuid4()))
        if not c.get("path"):
            c["path"] = app.media.save(user["id"], "b_", c["data"], c["suffix"], key=c["req"], ready=c.get("ready", False))
        ai = c["ai"] if c["ai"].get("ok") else {}                          # 기록용: AI 1등 답
        rows.append({"name": c["name"], "category": c["cat"], "subcategory": c["sub"], "color": c["color"],
                     "warmth": c["warmth"], "rain_ok": c["rain_ok"], "material": c["material"],
                     "image_path": c["path"], "notes": c["notes"].strip(), "ai_category": ai.get("category"),
                     "ai_subcategory": ai.get("subcategory"), "ai_confidence": ai.get("confidence"),
                     "request_id": c["req"]})
    app.store.create_wardrobe_items(user["id"], rows)     # 저장소가 지난 추천을 비운다 (screens/context.py)


def render(app, user):
    """내 옷장 > 직접 추가. 사진을 올리면 AI가 채운 카드를 한 장씩 확인하고 저장한다 (1장이어도 같은 화면)."""
    ss = st.session_state
    st.html(BA_CSS)
    ss.ba_names = {x["name"] for x in app.wardrobe(user["id"])}
    for kind, text in ss.ba_flash:      # 알림은 떠 있다 사라지게 (카드가 아래로 밀리지 않게)
        st.toast(text, icon={"success": "✅", "info": "ℹ️"}.get(kind, "⚠️"),
                 duration="long" if kind == "warning" else "short")
    ss.ba_flash = []
    cards = ss.ba_cards
    if (not cards or ss.ba_more) and not _uploader(app, cards):
        return
    ss.ba_idx = min(ss.ba_idx, len(cards) - 1)
    card = cards[ss.ba_idx]
    _progress(cards)
    ba_sync(card)
    ba_seed(card)
    with st.container(height=480, border=True, key=f"ba_scroll_{card['uid']}"):
        c_photo, c_form = st.columns([1, 1.2], gap="medium")
        with c_photo:
            _photo(cards, ss.ba_idx)
        with c_form:
            ba_fields(card)
    to_save = _actions(cards)
    if to_save:
        _save(app, user, cards, to_save)


def _uploader(app, cards):
    """올리기 칸 (처음이거나 [다시 올리기]를 눌렀을 때). 확인할 카드가 있으면 True."""
    ss = st.session_state
    if len(cards) < MAX_PHOTOS:
        label = ("저장할 의류를 넣어 주세요 (최대 10장까지)" if not cards
                 else f"사진 더 넣기 (지금 {len(cards)}장 · 최대 {MAX_PHOTOS}장까지)")
        files = st.file_uploader(label, type=["jpg", "jpeg", "png"], accept_multiple_files=True,
                                 key=f"ba_up_{ss.ba_nonce}")
        if files:
            ba_ingest(app, files)
            st.rerun()
    else:
        st.info(f"이미 {MAX_PHOTOS}장이에요. 사진을 빼거나 저장하면 더 넣을 수 있어요.")
    if not cards:
        st.caption("올리면 AI가 종류·색·이름을 먼저 채워 줘요. 한 장씩 확인하고 저장하세요.")
        return False
    with st.container(horizontal=True):
        if st.button("닫기", key="ba_more_close"):
            ss.ba_more = False
            st.rerun()
        st.button("지금 사진 모두 빼고 처음부터", key="ba_clear", type="tertiary", on_click=ba_clear)
    return True


def _progress(cards):
    n, done = len(cards), sum(c["confirmed"] for c in cards)
    with st.container(horizontal=True, vertical_alignment="bottom"):
        if n > 1:
            st.progress(done / n, text=f"📎 사진 {n}장 · 확인 {done} / {n}")
        else:
            note = "AI 분류 완료" if cards[0]["cands"] else "AI 분류를 못 한 사진은 종류를 직접 골라 주세요"
            st.caption(f"📎 사진 1장 · {note}")
        if st.button("목록", icon=":material/grid_view:", key="ba_list", disabled=n == 1):
            ba_list_dialog()


def _photo(cards, idx):
    """사진과 ‹ › 이동, 몇 번째인지·확인 여부 표시."""
    n, card, multi = len(cards), cards[idx], len(cards) > 1
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


def _actions(cards):
    """[확인하고 다음] [사진 빼기] [다시 올리기] [저장]. 저장을 눌렀으면 저장할 카드 목록, 아니면 []."""
    ss = st.session_state
    multi = len(cards) > 1
    all_done = all(c["confirmed"] for c in cards)
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
    return to_save if save else []


def _save(app, user, cards, to_save):
    ss = st.session_state
    try:
        ba_save(app, user, to_save)
    except Exception as e:
        st.error(f"저장하지 못했어요 ({type(e).__name__}). 잠시 뒤 다시 눌러 주세요.")
        return
    saved = {id(c) for c in to_save}
    ss.ba_cards = [c for c in cards if id(c) not in saved]
    ss.ba_idx = 0
    ss.ba_flash.append(("success", f"{len(to_save)}벌을 옷장에 넣었어요. '옷장 목록'에서 볼 수 있어요."
                        + (f" 남은 사진 {len(ss.ba_cards)}장도 확인해 주세요." if ss.ba_cards else "")))
    st.rerun()
