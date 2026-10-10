"""색상 이름 정규화 + 색 조합 점수.

사진으로 등록한 옷은 '#a3b2c1' 같은 hex로, 직접 입력한 옷은 한글·영어 이름으로 저장되므로
모두 같은 정규 이름(black, navy, beige …)으로 바꾼 뒤 무채색·유채색을 판단한다.
점수는 유채색 포인트 개수로 매겨 후보마다 차이가 나게 한다.
"""
from __future__ import annotations

import re

NEUTRALS = {"black", "white", "gray", "navy", "beige", "ivory", "brown", "khaki"}

# 화면에 보일 한글 색 이름 (웹 체험판 web/src/closet.js 의 COLOR_KO 와 같음)
COLOR_KO = {
    "black": "검정", "white": "흰색", "gray": "회색", "navy": "네이비", "beige": "베이지", "ivory": "아이보리",
    "brown": "브라운", "khaki": "카키", "blue": "파랑", "skyblue": "하늘색", "red": "빨강", "pink": "분홍",
    "orange": "주황", "yellow": "노랑", "green": "초록", "purple": "보라",
}

# 유채색 비교용 기준색 (RGB)
_PALETTE = {
    "navy": (30, 40, 80),
    "beige": (215, 195, 160),
    "ivory": (240, 234, 214),
    "brown": (110, 75, 45),
    "khaki": (125, 120, 80),
    "blue": (60, 110, 180),
    "skyblue": (150, 190, 230),
    "red": (190, 40, 40),
    "pink": (230, 150, 170),
    "orange": (230, 130, 40),
    "yellow": (235, 210, 60),
    "green": (50, 130, 70),
    "purple": (120, 70, 150),
}

_ALIASES = [
    # (포함 문자열들, 정규 이름) — 위에서부터 먼저 매칭
    (("하늘", "스카이", "skyblue", "light blue"), "skyblue"),
    (("네이비", "남색", "navy"), "navy"),
    (("아이보리", "ivory"), "ivory"),
    (("베이지", "크림", "beige", "cream", "camel", "카멜"), "beige"),
    (("카키", "올리브", "khaki", "olive"), "khaki"),
    (("갈색", "브라운", "brown"), "brown"),
    (("검정", "검은", "블랙", "black"), "black"),
    (("흰", "화이트", "white"), "white"),
    (("회색", "그레이", "차콜", "gray", "grey", "charcoal"), "gray"),
    (("파랑", "파란", "블루", "청", "blue", "denim"), "blue"),
    (("빨강", "빨간", "레드", "red"), "red"),
    (("분홍", "핑크", "pink"), "pink"),
    (("주황", "오렌지", "orange"), "orange"),
    (("노랑", "노란", "옐로", "yellow"), "yellow"),
    (("초록", "녹색", "그린", "green"), "green"),
    (("보라", "퍼플", "purple"), "purple"),
]

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def hex_to_name(hex_str):
    m = _HEX.match(hex_str.strip())
    if not m:
        return None
    h = m.group(1)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    mx, mn = max(r, g, b), min(r, g, b)
    if mx - mn < 28:  # 채도 낮음 → 무채색
        light = (mx + mn) / 2
        if light < 60:
            return "black"
        if light > 215:
            return "white"
        return "gray"
    return min(_PALETTE, key=lambda k: sum((a - b) ** 2 for a, b in zip(_PALETTE[k], (r, g, b))))


def normalize_color(value):
    """hex / 한글 / 영어 색 이름을 정규 이름으로. 모르면 None."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if _HEX.match(s):
        return hex_to_name(s)
    for keys, name in _ALIASES:
        if any(k in s for k in keys):
            return name
    return None


def _is_denim(piece):
    sub = str(piece.get("subcategory") or "")
    return (
        str(piece.get("material") or "").lower() == "denim"
        or sub in ("청바지", "데님재킷")
        or "데님" in str(piece.get("name") or "")
    )


def color_score(pieces):
    """(점수, 설명). 유채색 포인트 1개가 가장 높고, 유채색이 많을수록 감점.

    데님(청바지·데님재킷)은 파란색이어도 무채색처럼 취급한다.
    """
    accents = set()
    for p in pieces:
        c = normalize_color(p.get("color"))
        if c is None or c in NEUTRALS or _is_denim(p):
            continue
        accents.add(c)
    n = len(accents)
    if n == 0:
        return 0.95, "무채색 위주라 무난한 색 조합입니다."
    if n == 1:
        accent = next(iter(accents))
        return 1.0, f"무채색에 포인트 컬러({COLOR_KO.get(accent, accent)}) 하나를 더한 조합입니다."
    if n == 2:
        if accents in ({"blue", "skyblue"}, {"red", "pink"}):
            return 0.9, "같은 계열 색을 겹친 톤온톤 조합입니다."
        return 0.75, "유채색이 2개라 색이 다소 강할 수 있어요."
    return 0.5, "유채색이 3개 이상이라 색 조합이 산만할 수 있어요."
