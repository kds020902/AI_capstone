from core.catalog import FEMALE_STARTER, MALE_STARTER
from core.eligibility import THERMAL_TYPES

THERMAL = THERMAL_TYPES  # 앱 프로필·웹 체험판과 같은 체감 성향 5단계


def starter_items(gender):
    rows = FEMALE_STARTER if gender == "female" else MALE_STARTER
    items = [
        dict(id=i, name=r[0], category=r[1], subcategory=r[2], color=r[3],
             warmth=r[4], rain_ok=r[5], material=r[6])
        for i, r in enumerate(rows, 1)
    ]
    return list(reversed(items))  # DB는 최근 등록 순(id DESC)으로 돌려준다
