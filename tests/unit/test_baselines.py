import math

import numpy as np
import pytest

from evaluation import baselines


class Transform:
    """Minimal stand-in for an affine transform (north-up, 30 m cells)."""

    def __init__(self, xmin: float, ymax: float, cell: float):
        self.a, self.c, self.e, self.f = cell, xmin, -cell, ymax


def test_mean_wind_points_downwind():
    # Wind from the west (270) pushes the fire east (90)
    speed, toward = baselines.mean_wind([10.0, 10.0], [270.0, 270.0])
    assert speed == pytest.approx(10.0)
    assert toward == pytest.approx(90.0)


def test_mean_wind_opposite_directions_cancel():
    speed, _ = baselines.mean_wind([10.0, 10.0], [0.0, 180.0])
    assert speed == pytest.approx(0.0, abs=1e-9)


def test_length_to_breadth_grows_with_wind_and_is_bounded():
    lbs = [baselines.length_to_breadth(u) for u in (0, 5, 10, 20, 100)]
    assert lbs[0] == pytest.approx(1.0, abs=0.01)
    assert lbs == sorted(lbs)
    assert lbs[-1] == baselines.MAX_LB


def test_wind_ellipse_area_and_direction():
    shape, cell = (200, 200), 30.0
    tr = Transform(0.0, shape[0] * cell, cell)
    ign = (3000.0, 3000.0)
    area = 50 * 1e4  # 50 ha
    mask = baselines.wind_ellipse(shape, tr, ign, area, [15.0] * 3, [270.0] * 3)

    assert mask.sum() * cell * cell == pytest.approx(area, rel=0.05)
    rows, cols = np.nonzero(mask)
    cx = tr.c + (cols.mean() + 0.5) * tr.a
    cy = tr.f + (rows.mean() + 0.5) * tr.e
    assert cx > ign[0]  # pushed east, downwind of a westerly
    assert math.isclose(cy, ign[1], abs_tol=cell)
