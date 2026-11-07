import pytest

from rules.core.stage import Change, Stage, by_area, by_facility, by_wind, judge_stage, stage_change

INIT, S1, S2 = Stage.INITIAL, Stage.STAGE1, Stage.STAGE2


@pytest.mark.parametrize(
    ("judge", "value", "expected"),
    [
        (by_area, 9.9, INIT),
        (by_area, 10, S1),
        (by_area, 100, S2),
        (by_wind, 2.9, INIT),
        (by_wind, 3, S1),
        (by_wind, 11, S2),
        (by_facility, 0, INIT),
        (by_facility, 1, S1),
        (by_facility, 20, S2),
    ],
)
def test_factor_bounds_are_inclusive(judge, value, expected):
    assert judge(value) is expected


def test_example_now_every_factor_decides():
    # 12:50 예시: 48.5 ha · 3.4 m/s · 2동 → 세 요소 모두 1단계
    j = judge_stage(48.5, 3.4, 2)
    assert j.stage is S1
    assert j.decided_by == ("area", "wind", "facility")


def test_example_p5_highest_factor_wins_and_missing_wind_is_skipped():
    j = judge_stage(363, None, 2)
    assert (j.stage, j.by_wind, j.by_facility) == (S2, None, S1)
    assert j.decided_by == ("area",)


def test_initial_has_no_deciding_factor():
    assert judge_stage(3, 1.8, 0).decided_by == ()


@pytest.mark.parametrize(
    ("prev", "now", "expected"),
    [(None, S1, Change.NEW), (INIT, S1, Change.UP), (S1, S1, Change.SAME), (S2, S1, Change.DOWN)],
)
def test_stage_change(prev, now, expected):
    assert stage_change(prev, now) is expected
