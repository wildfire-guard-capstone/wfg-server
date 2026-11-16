"""P1 ~ P8 경계: 도착시간 등고선 모양, 면적은 30 m 칸 수와 같게(perimeter_run.smooth_range)."""

import numpy as np
import pytest

pytest.importorskip("scipy")
from affine import Affine  # noqa: E402
from shapely.geometry import Point  # noqa: E402

from spread import perimeter_run as pr  # noqa: E402

T = Affine(30, 0, 1_000_000, 0, -30, 2_000_000)


def _res(toa: np.ndarray, start: np.ndarray) -> pr.RunResult:
    return pr.RunResult(
        run_dir=None, t_start_s=0.0, t_stop_s=8 * 3600.0, adj=1.0, wall_s=0.0,
        toa=toa, start_mask=start, transform=T, cell_ha=0.09,
    )  # fmt: skip


def _ellipse(h=160, w=160):
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot(yy - h / 2 + 0.3, (xx - w / 2 + 0.7) * 1.4) * 30  # m
    toa = r / 0.4  # 0.4 m/s
    toa[r > 2200] = np.nan
    return toa, r < 50


def test_area_matches_burned_cells_and_edge_is_not_stairs():
    res = _res(*_ellipse())
    for t in (1800.0, 3600.0, 3 * 3600.0):
        cells = pr.fill_holes_mask(res.burned_by(t)).sum() * 900
        g = pr.smooth_range(res, t)
        assert g.geom_type == "Polygon"
        assert abs(g.area - cells) / cells < 0.005  # 칸 면적과 0.5 % 안
        xy = np.asarray(g.exterior.coords)
        d = np.diff(xy, axis=0)
        axis_aligned = np.mean((np.abs(d[:, 0]) < 1e-6) | (np.abs(d[:, 1]) < 1e-6))
        assert axis_aligned < 0.5  # 칸 테두리(전부 가로 · 세로)가 아님


def test_ranges_stay_nested_and_hole_is_filled():
    toa, start = _ellipse()
    toa[70:80, 70:80] = np.nan  # 안 탄 섬 → 범위에서는 메움
    rng = pr.ranges(_res(toa, start), Point(0, 0).buffer(1), hours=4)
    for a, b in zip(rng[1:], rng[2:], strict=False):
        assert b.buffer(0.01).contains(a)
    assert all(len(g.interiors) == 0 for g in rng[1:] if g.geom_type == "Polygon")


def test_point_start_single_cell():
    toa = np.full((40, 40), np.nan)
    start = np.zeros((40, 40), bool)
    start[20, 20] = True
    g = pr.smooth_range(_res(toa, start), 600.0)
    assert abs(g.area - 900) / 900 < 0.01  # 한 칸 = 0.09 ha


def test_nothing_burned_is_empty():
    toa = np.full((10, 10), np.nan)
    assert pr.smooth_range(_res(toa, np.zeros((10, 10), bool)), 600.0).is_empty
