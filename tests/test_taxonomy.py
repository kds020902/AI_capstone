"""분류 체계 일관성 테스트."""
import csv

from core.catalog import FEMALE_STARTER, MALE_STARTER
from core.taxonomy import LAYER_PAIRS, MAIN_CATEGORIES, PURPOSES, RULES, SUBCATEGORY_TO_MAIN, ROOT


def test_no_path_characters_in_class_names():
    """클래스 이름은 학습 데이터 폴더명이 되므로 '/'나 '\\'를 쓸 수 없다."""
    for name in RULES:
        assert "/" not in name and "\\" not in name, name


def test_rules_are_well_formed():
    for name, r in RULES.items():
        assert r["main_category"] in MAIN_CATEGORIES, name
        assert 1 <= r["default_warmth"] <= 5, name
        assert r["min_temp"] < r["max_temp"], name
        assert r["blocked_purposes"] <= set(PURPOSES), name


def test_starter_items_use_known_classes():
    for row in MALE_STARTER + FEMALE_STARTER:
        assert row[2] in SUBCATEGORY_TO_MAIN, row
        assert SUBCATEGORY_TO_MAIN[row[2]] == row[1], row
    assert len(MALE_STARTER) == 30 and len(FEMALE_STARTER) == 30


def test_starter_covers_every_purpose():
    """스타터 옷장만으로 운동·한겨울 추천이 가능해야 한다."""
    for rows in (MALE_STARTER, FEMALE_STARTER):
        subs = {r[2] for r in rows}
        assert {"기능성 티셔츠", "러닝화", "조거팬츠"} <= subs
        assert {"패딩", "코트"} <= subs


def test_dataset_label_map_targets_exist():
    path = ROOT / "data" / "dataset_label_map.csv"
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["subcategory"]:
                assert row["subcategory"] in SUBCATEGORY_TO_MAIN, row
                assert SUBCATEGORY_TO_MAIN[row["subcategory"]] == row["main_category"], row
            assert row["main_category"] in MAIN_CATEGORIES, row


def test_layer_pairs_are_known_tops():
    for inner, over in LAYER_PAIRS:
        assert SUBCATEGORY_TO_MAIN[inner] == "상의" and SUBCATEGORY_TO_MAIN[over] == "상의"
        assert (over, inner) not in LAYER_PAIRS  # 안·밖이 뒤집힌 조합이 같이 있으면 역할을 정할 수 없음


def test_starter_has_no_style_field():
    """튜플 = (이름, 대분류, 세부분류, 색상, 두께, 비 적합, 소재)."""
    for row in MALE_STARTER + FEMALE_STARTER:
        assert len(row) == 7 and row[3] not in ("casual", "formal", "sporty"), row
