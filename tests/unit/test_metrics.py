import math

import numpy as np
import pytest

from evaluation import metrics


def grid(*cells: tuple[int, int], shape=(10, 10)) -> np.ndarray:
    a = np.zeros(shape, dtype=bool)
    for r, c in cells:
        a[r, c] = True
    return a


def block(r0, r1, c0, c1, shape=(10, 10)) -> np.ndarray:
    a = np.zeros(shape, dtype=bool)
    a[r0:r1, c0:c1] = True
    return a


def test_perfect_prediction():
    truth = block(2, 6, 2, 6)
    s = metrics.score(truth, truth)
    assert s.iou == s.f1 == s.f2 == s.precision == s.recall == 1.0
    assert s.kappa == pytest.approx(1.0)
    assert s.adi == 0.0
    assert s.area_ratio == 1.0


def test_known_overlap():
    # truth 4x4 = 16 cells, pred shifted by 2 columns -> tp 8, fp 8, fn 8
    truth = block(2, 6, 2, 6)
    pred = block(2, 6, 4, 8)
    c = metrics.Confusion.of(pred, truth)
    assert (c.tp, c.fp, c.fn) == (8, 8, 8)
    s = metrics.score(pred, truth)
    assert s.iou == pytest.approx(8 / 24)
    assert s.f1 == pytest.approx(0.5)
    assert s.f2 == pytest.approx(0.5)
    assert (s.adi, s.adi_over, s.adi_under) == (2.0, 1.0, 1.0)


def test_overprediction_raises_recall_but_lowers_iou():
    truth = block(4, 6, 4, 6)
    tight = truth.copy()
    wide = block(0, 10, 0, 10)
    s_tight, s_wide = metrics.score(tight, truth), metrics.score(wide, truth)
    assert s_wide.recall == 1.0
    assert s_wide.iou < s_tight.iou
    assert s_wide.f2 > s_wide.f1
    assert s_wide.area_ratio == pytest.approx(25.0)


def test_f2_weights_recall():
    truth = block(0, 4, 0, 4)
    under = block(0, 4, 0, 2)  # misses half, no false alarm
    s = metrics.score(under, truth)
    assert s.precision == 1.0
    assert s.recall == 0.5
    assert s.f2 < s.f1


def test_empty_rasters_are_nan_not_errors():
    empty = grid()
    s = metrics.score(empty, empty)
    assert math.isnan(s.iou) and math.isnan(s.precision) and math.isnan(s.recall)


def test_shape_mismatch_raises():
    with pytest.raises(ValueError):
        metrics.score(grid(shape=(5, 5)), grid(shape=(6, 6)))


def test_skill_score():
    assert metrics.skill(0.6, 0.2) == pytest.approx(0.5)
    assert metrics.skill(0.2, 0.2) == 0.0
