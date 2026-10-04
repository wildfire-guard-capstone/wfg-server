import numpy as np
import pytest

from spread import slices

NAN = np.nan


def toa_grid() -> np.ndarray:
    # 3 x 4 time-of-arrival raster (s); NaN = never burned
    return np.array(
        [
            [0.0, 1800.0, 3600.0, NAN],
            [3000.0, 7200.0, 10800.0, NAN],
            [14400.0, 28800.0, NAN, NAN],
        ]
    )


def test_hourly_slices_are_nested_and_cumulative():
    p = slices.hourly(toa_grid(), 8)
    assert len(p) == 8
    assert [int(m.sum()) for m in p] == [4, 5, 6, 7, 7, 7, 7, 8]
    for small, big in zip(p, p[1:], strict=False):
        assert not (small & ~big).any()


def test_burned_ignores_nan_and_negative():
    toa = np.array([[NAN, -9999.0, 0.0, 5.0]])
    assert slices.burned(toa, 10.0).tolist() == [[False, False, True, True]]


def test_area_matched_cuts_at_target_cell_count():
    mask, t_star = slices.area_matched(toa_grid(), 5)
    assert t_star == 7200.0
    assert mask.sum() == 5


def test_area_matched_not_reached_returns_everything_burned():
    mask, t_star = slices.area_matched(toa_grid(), 50)
    assert t_star is None
    assert mask.sum() == 8


def test_area_matched_empty_raster():
    mask, t_star = slices.area_matched(np.full((2, 2), NAN), 3)
    assert t_star is None
    assert not mask.any()


@pytest.mark.parametrize("target", [1, 3, 8])
def test_area_matched_never_undershoots(target):
    mask, _ = slices.area_matched(toa_grid(), target)
    assert mask.sum() >= target
