"""'직접 추가' 여러 장 등록 계산 (core/batch_add.py)."""
from core.batch_add import (
    MAX_PHOTOS, after_remove, default_name, next_unconfirmed, step, take_room, unique_name,
)


def test_take_room_keeps_first_up_to_ten():
    files = list(range(12))
    assert MAX_PHOTOS == 10
    assert take_room(0, files) == (list(range(10)), 2)
    assert take_room(7, files) == ([0, 1, 2], 9)
    assert take_room(10, files) == ([], 12)
    assert take_room(3, [0, 1]) == ([0, 1], 0)


def test_step_wraps_both_ways():
    assert step(0, 10, 1) == 1
    assert step(0, 10, -1) == 9        # 1번에서 이전 → 10번
    assert step(9, 10, 1) == 0         # 10번에서 다음 → 1번
    assert step(0, 1, 1) == 0
    assert step(0, 0, 1) == 0


def test_next_unconfirmed_goes_round_and_stops_when_done():
    assert next_unconfirmed([True, False, False], 0) == 1
    assert next_unconfirmed([False, True, True, False], 3) == 0       # 끝에서 처음으로
    assert next_unconfirmed([True, True, False], 2) == 2              # 남은 게 자기 자신뿐
    assert next_unconfirmed([True, True, True], 1) is None
    assert next_unconfirmed([], 0) is None


def test_after_remove_keeps_position():
    assert after_remove(3, 9) == 3
    assert after_remove(9, 9) == 8      # 마지막을 빼면 새 마지막
    assert after_remove(0, 0) == 0


def test_unique_and_default_names():
    taken = {"검정 니트", "검정 니트 2", "흰색 셔츠"}
    assert unique_name("검정 니트", taken) == "검정 니트 3"
    assert unique_name(" 흰색 셔츠 ", taken) == "흰색 셔츠 2"
    assert unique_name("회색 후드티", taken) == "회색 후드티"
    assert default_name("검정", "니트", taken) == "검정 니트 3"
    assert default_name("", "니트", set()) == "니트"
