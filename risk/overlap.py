"""폴리곤과 격자 · 구역의 겹침 면적 비율(「산불 발생 후 위험도 정의」 사안 3).

    πc = (폴리곤 ∩ 칸 c의 면적) ÷ (폴리곤 중 격자 안에 든 면적),  Σ πc = 1

면적과 무관한 값은 칸마다 등급으로 바꾼 뒤 πc로 가중평균하고, 인원처럼 더해지는 값은
구역 j의 인원 × (폴리곤 ∩ 구역 j) ÷ 구역 j 면적을 더한다(Goodchild & Lam 1980).
"""

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class Grid:
    """북쪽이 위인 정사각 격자. (x0, y0) = 왼쪽 위 모서리, 칸 크기 cs(m), 크기 (h, w)."""

    x0: float
    y0: float
    cs: float
    h: int
    w: int

    def cell_box(self, r: np.ndarray, c: np.ndarray):
        x = self.x0 + c * self.cs
        y = self.y0 - r * self.cs
        return shapely.box(x, y - self.cs, x + self.cs, y)


@dataclass(frozen=True)
class Weights:
    rows: np.ndarray
    cols: np.ndarray
    frac: np.ndarray  # Σ = 1
    covered: float  # 폴리곤 면적 중 격자 안에 든 비율(1이면 다 듦)


def grid_weights(geom: BaseGeometry, grid: Grid) -> Weights:
    """폴리곤이 걸친 칸과 칸별 겹침 비율. 폴리곤이 격자 밖이면 칸이 0개(covered 0)."""
    minx, miny, maxx, maxy = geom.bounds
    c0 = max(int(np.floor((minx - grid.x0) / grid.cs)), 0)
    c1 = min(int(np.ceil((maxx - grid.x0) / grid.cs)), grid.w)
    r0 = max(int(np.floor((grid.y0 - maxy) / grid.cs)), 0)
    r1 = min(int(np.ceil((grid.y0 - miny) / grid.cs)), grid.h)
    empty = Weights(np.array([], int), np.array([], int), np.array([]), 0.0)
    if c1 <= c0 or r1 <= r0 or geom.is_empty:
        return empty
    rr, cc = np.mgrid[r0:r1, c0:c1]
    rr, cc = rr.ravel(), cc.ravel()
    boxes = grid.cell_box(rr, cc)
    shapely.prepare(geom)
    inside = shapely.contains_properly(geom, boxes)
    area = np.where(inside, grid.cs * grid.cs, 0.0)
    edge = ~inside & shapely.intersects(geom, boxes)
    if edge.any():
        area[edge] = shapely.area(shapely.intersection(boxes[edge], geom))
    keep = area > 0
    total = area[keep].sum()
    if total <= 0:
        return empty
    return Weights(rr[keep], cc[keep], area[keep] / total, float(total / geom.area))


def weighted_mean(values: np.ndarray, w: Weights) -> float | None:
    """칸 값의 가중평균. NaN 칸은 빼고 남은 비율로 다시 나눈다. 칸이 없거나 모두 NaN이면 None."""
    if w.frac.size == 0:
        return None
    v = values[w.rows, w.cols].astype("float64")
    ok = ~np.isnan(v)
    if not ok.any():
        return None
    return float((v[ok] * w.frac[ok]).sum() / w.frac[ok].sum())


def zone_sum(zones: Iterable[tuple[BaseGeometry, float]], geom: BaseGeometry) -> tuple[float, list]:
    """Σ 인원ⱼ × (geom ∩ 구역ⱼ 면적) ÷ 구역ⱼ 면적. 돌려주는 것: (합계, 겹친 구역 목록)."""
    total, hits = 0.0, []
    shapely.prepare(geom)
    for i, (z, n) in enumerate(zones):
        if z.is_empty or z.area <= 0 or not geom.intersects(z):
            continue
        share = geom.intersection(z).area / z.area
        if share <= 0:
            continue
        total += n * share
        hits.append((i, share, n * share))
    return total, hits
