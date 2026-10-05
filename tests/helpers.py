from core.catalog import FEMALE_STARTER, MALE_STARTER

THERMAL = {
    "추위를 잘 탐": (1.0, 0.0),
    "추위를 조금 탐": (0.6, 0.1),
    "보통": (0.0, 0.0),
    "더위를 조금 탐": (0.1, 0.6),
    "더위를 잘 탐": (0.0, 1.0),
}


def starter_items(gender):
    rows = FEMALE_STARTER if gender == "female" else MALE_STARTER
    items = [
        dict(id=i, name=r[0], category=r[1], subcategory=r[2], color=r[3],
             warmth=r[4], rain_ok=r[5], material=r[6])
        for i, r in enumerate(rows, 1)
    ]
    return list(reversed(items))  # DB는 최근 등록 순(id DESC)으로 돌려준다
