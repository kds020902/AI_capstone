"""'직접 추가'에서 사진 여러 장을 한 번에 등록할 때 쓰는 계산.

화면(app.py)과 떨어뜨려 두어 시험하기 쉽게 했다. 카드 순서는 올린 순서이고,
이동은 처음과 끝이 이어진다 (1번에서 이전 → 마지막, 마지막에서 다음 → 1번).
"""
from __future__ import annotations

MAX_PHOTOS = 10   # 한 번에 확인·저장하는 최대 장수


def take_room(existing: int, incoming: list) -> tuple[list, int]:
    """이미 existing 장이 있을 때 새로 넣을 수 있는 만큼만 앞에서부터 받는다. (받은 것, 빠진 장수)"""
    room = max(0, MAX_PHOTOS - existing)
    taken = list(incoming[:room])
    return taken, len(incoming) - len(taken)


def step(i: int, n: int, delta: int) -> int:
    """i 번째에서 delta 만큼 이동 (끝과 처음이 이어짐)."""
    return (i + delta) % n if n else 0


def next_unconfirmed(confirmed: list[bool], i: int) -> int | None:
    """i 다음부터 한 바퀴 돌며 아직 확인 안 한 카드 번호. 모두 확인했으면 None."""
    n = len(confirmed)
    for k in range(1, n + 1):
        j = (i + k) % n
        if not confirmed[j]:
            return j
    return None


def after_remove(i: int, n_after: int) -> int:
    """i 번째 카드를 뺀 뒤 보여 줄 카드 번호 (같은 자리, 끝이었으면 새 끝)."""
    return min(i, n_after - 1) if n_after > 0 else 0


def unique_name(base: str, taken) -> str:
    """이미 있는 이름이면 뒤에 2, 3 … 을 붙인다 (예: '검정 니트 2')."""
    base = base.strip()
    taken = set(taken)
    if base not in taken:
        return base
    k = 2
    while f"{base} {k}" in taken:
        k += 1
    return f"{base} {k}"


def default_name(color_ko: str, subcategory: str, taken) -> str:
    """자동 이름 '{색} {종류}' (겹치면 번호)."""
    return unique_name(f"{color_ko or ''} {subcategory}", taken)
