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
