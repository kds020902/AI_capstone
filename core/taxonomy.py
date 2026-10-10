"""의류 분류 체계 + 추천 규칙표.

data/clothing_taxonomy.csv 하나가 분류 클래스, 대분류 매핑, 업로드 기본값,
기온 허용 범위, 목적별 금지 규칙의 '단일 출처'다.
규칙을 바꾸고 싶으면 코드가 아니라 CSV를 고치면 된다.

- min_temp / max_temp : 사용자 체감 보정이 끝난 '유효 기온'(℃) 기준 착용 허용 범위
- blocked_purposes    : 이 세부분류를 추천하지 않는 외출 목적 ("|" 구분)
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_CSV = ROOT / "data" / "clothing_taxonomy.csv"

MAIN_CATEGORIES = ["상의", "하의", "원피스", "아우터", "신발"]
PURPOSES = ["등교", "데이트", "운동", "격식"]

# 옛 DB·라벨에 남아 있을 수 있는 클래스명 → 현재 클래스명 ("/"는 학습 데이터 폴더명으로 쓸 수 없음)
LEGACY_RENAMES = {"야상/필드재킷": "야상·필드재킷"}

# 같이 입히지 않는 조합 (순서 무관)
INCOMPATIBLE_PAIRS = {
    frozenset(p) for p in [
        ("블레이저", "후드티"), ("블레이저", "조거팬츠"), ("블레이저", "반바지"),
        ("블레이저", "민소매"), ("블레이저", "기능성 티셔츠"), ("블레이저", "러닝화"),
        ("코트", "기능성 티셔츠"), ("코트", "러닝화"), ("코트", "반바지"),
        ("로퍼", "조거팬츠"), ("로퍼", "기능성 티셔츠"),
        ("원피스", "러닝화"), ("셔츠원피스", "러닝화"), ("니트원피스", "러닝화"),
        ("구두", "조거팬츠"), ("구두", "반바지"), ("구두", "레깅스"), ("구두", "기능성 티셔츠"), ("구두", "후드티"),
        ("힐", "조거팬츠"), ("힐", "기능성 티셔츠"), ("힐", "레깅스"),
        ("블레이저", "레깅스"), ("블레이저", "슬리퍼"),
    ]
}

# 상의를 두 벌 겹쳐 입는 조합 (안에 입는 옷, 위에 입는 옷). 이 표에 있는 조합만 레이어드 코디로 만든다.
LAYER_PAIRS = {
    ("셔츠", "니트"), ("셔츠", "맨투맨"),
    ("블라우스", "니트"),
    ("반팔 티셔츠", "셔츠"),  # 셔츠를 걸쳐 입기
}


def _load():
    rows = {}
    with open(TAXONOMY_CSV, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            name = r["subcategory"].strip()
            if "/" in name or "\\" in name:
                raise ValueError(f"세부분류 이름에 경로 문자를 쓸 수 없습니다: {name}")
            blocked = {p.strip() for p in (r.get("blocked_purposes") or "").split("|") if p.strip()}
            rows[name] = {
                "main_category": r["main_category"].strip(),
                "default_warmth": int(r["default_warmth"]),
                "default_rain_ok": bool(int(r["default_rain_ok"])),
                "min_temp": float(r["min_temp"]),
                "max_temp": float(r["max_temp"]),
                "blocked_purposes": blocked,
            }
    return rows


RULES = _load()
SUBCATEGORIES = list(RULES)
SUBCATEGORY_TO_MAIN = {k: v["main_category"] for k, v in RULES.items()}
NUM_SUBCATEGORIES = len(SUBCATEGORIES)


def canonical_subcategory(name):
    if not name:
        return name
    return LEGACY_RENAMES.get(name, name)


def rule_for(subcategory):
    return RULES.get(canonical_subcategory(subcategory))


def main_category_for_subcategory(subcategory):
    r = rule_for(subcategory)
    return r["main_category"] if r else None


# ---------------------------------------------------------------- 계절
# 계절마다 낮의 대표 유효 기온(℃). 옷 종류의 착용 기온 범위가 이 중 하나라도 품으면 그 계절 옷으로 본다
# (봄·가을은 쌀쌀할 때와 선선할 때 두 값). 규칙표만으로 정해지므로 사용자가 따로 입력하지 않아도 된다.
SEASON_TEMPS = {"여름": (28,), "봄·가을": (11, 16), "겨울": (0,)}
SEASONS = list(SEASON_TEMPS)


def seasons_for(subcategory):
    """옷 종류로 정한 계절 목록 (규칙표에 없는 종류는 사계절)."""
    r = rule_for(subcategory)
    if r is None:
        return list(SEASONS)
    return [s for s, temps in SEASON_TEMPS.items() if any(r["min_temp"] <= t <= r["max_temp"] for t in temps)]


def manual_seasons(item):
    """사용자가 직접 고른 계절 ('여름|겨울' 형식으로 저장). 없으면 빈 목록."""
    return [s for s in SEASONS if s in (item.get("seasons") or "").split("|")]


def item_seasons(item):
    return manual_seasons(item) or seasons_for(item.get("subcategory"))


def season_label(seasons):
    return "사계절" if len(seasons) == len(SEASONS) else ", ".join(seasons)


def current_season(month):
    return "여름" if month in (6, 7, 8) else "겨울" if month in (12, 1, 2) else "봄·가을"
