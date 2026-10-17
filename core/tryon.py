"""가상 착용(CatVTON) 연결 도우미. 모델은 Hugging Face ZeroGPU Space(tryon_space/)에서 돌고,
앱은 그 Space 화면을 띄우면서 옷 사진 주소(짧게 유효한 서명 주소)와 입히는 부위만 넘긴다."""
from __future__ import annotations

import re
from urllib.parse import urlencode

# CatVTON 이 지원하는 입히는 부위. 신발은 학습 데이터에 없어 지원하지 않는다.
KIND_BY_CATEGORY = {"상의": "upper", "하의": "lower", "원피스": "overall", "아우터": "outer"}
MAX_GARMENTS = 2
_PICK_FIRST = {"upper": 0, "overall": 0, "lower": 1, "outer": 2}     # 기본으로 고를 때: 상의·원피스 → 하의 → 아우터
_HF_PAGE = re.compile(r"^https://huggingface\.co/spaces/([^/?#]+)/([^/?#]+)/?$")


def tryon_kind(category, subcategory=None):
    """옷장 대분류 → CatVTON 부위 (upper/lower/overall/outer). 입힐 수 없는 종류면 None."""
    return KIND_BY_CATEGORY.get(category)


def tryon_supported(items):
    return [x for x in items if tryon_kind(x.get("category"))]


def tryon_order(items):
    """입힐 순서: 하의를 마지막에 (상의·원피스·아우터 → 하의). 지원하지 않는 옷은 뺀다."""
    return sorted(tryon_supported(items), key=lambda x: tryon_kind(x.get("category")) == "lower")[:MAX_GARMENTS]


def tryon_default(items):
    """여러 벌 중 기본으로 입혀 볼 옷 (최대 2벌): 상의·원피스와 하의를 먼저 고른다."""
    picked = sorted(tryon_supported(items), key=lambda x: _PICK_FIRST[tryon_kind(x.get("category"))])
    return tryon_order(picked[:MAX_GARMENTS])


def space_addresses(setting):
    """TRYON_SPACE_URL → (앱 안 창에 띄울 주소, 새 창으로 열 주소).

    Space 페이지 주소(https://huggingface.co/spaces/아이디/이름)를 넣으면 앱 안에는 https://아이디-이름.hf.space 를
    띄우고, 새 창은 Hugging Face 페이지로 연다. Hugging Face 페이지에서는 방문자의 Hugging Face 로그인이 적용돼
    하루 사용량이 비로그인(IP 기준)보다 늘어난다. 페이지의 ?쿼리는 처음 열 때 Space 화면으로 그대로 전달된다.
    https://...hf.space 주소를 직접 넣으면 둘 다 그 주소를 쓴다.
    """
    s = setting.strip()
    m = _HF_PAGE.match(s)
    if m:
        owner, name = m.groups()
        sub = re.sub(r"[^a-z0-9]+", "-", f"{owner}-{name}".lower()).strip("-")
        return f"https://{sub}.hf.space/", f"https://huggingface.co/spaces/{owner}/{name}"
    s = s.rstrip("/") + "/"
    return s, s


def tryon_link(base_url, garments=()):
    """garments: [(사진 주소 또는 None, 부위)] 최대 2개. 주소가 없으면 Space 화면에서 직접 올린다."""
    params = {}
    for i, (url, kind) in enumerate(list(garments)[:MAX_GARMENTS]):
        suffix = "" if i == 0 else str(i + 1)
        if url:
            params["garment" + suffix] = url
        if kind:
            params["kind" + suffix] = kind
    return base_url + ("?" + urlencode(params) if params else "")
