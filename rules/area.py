"""R-15 면적: 범위 도형의 면적을 재고, 잰 면적으로 기준을 넘는 구간을 찾는다.

좌표는 EPSG:5179(m)라 면적(m²) ÷ 10,000 = ha.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

RANGE_COUNT = 9  # P0(현재 화선) ~ P8


def range_areas(ranges: Sequence[BaseGeometry]) -> list[float]:
    """P0 ~ P8 범위의 면적(ha)을 잰다. 결과의 i번째 = Pi.

    1. 범위가 9개(P0 ~ P8)가 아니면 오류
    2. 범위마다 면적(m²) ÷ 10,000
    3. P0가 발화점(점)이면 면적은 0
    """
    # 1
    if len(ranges) != RANGE_COUNT:
        raise ValueError(f"범위는 P0 ~ P8 {RANGE_COUNT}개여야 한다: {len(ranges)}개")
    return [geom.area / 10_000 for geom in ranges]  # 2 · 3


@dataclass(frozen=True)
class Crossing:
    threshold_ha: float
    p: int  # 면적이 기준에 처음 닿는 구간(0시 뒤 몇 시간)
    area_ha: float  # 그 구간의 면적


def first_over(
    now_ha: float, slices: Sequence[tuple[int, float]], thresholds_ha: Iterable[float]
) -> list[Crossing]:
    """지금 면적보다 큰 기준마다, 면적이 그 기준 이상(≥)이 되는 첫 구간을 찾는다.

    `slices` = P1 ~ P8의 (p, 면적 ha). 시각은 나중에 변환 층이 붙인다(0시 + p시간).

    1. 구간을 P1 → P8 순서로 놓고, 기준은 겹친 것을 빼고 작은 순으로 놓는다
    2. 지금 면적이 이미 기준 이상이면 그 기준은 건너뛴다
    3. 나머지 기준마다 면적이 기준 이상이 되는 첫 구간을 찾는다
    4. P8까지 못 넘으면 그 기준은 결과에 넣지 않는다
    """
    # 1
    ordered = sorted(slices)
    crossings = []
    for threshold in sorted(set(thresholds_ha)):
        if now_ha >= threshold:  # 2
            continue
        hit = next(((p, ha) for p, ha in ordered if ha >= threshold), None)  # 3
        if hit is not None:  # 4
            crossings.append(Crossing(threshold, *hit))
    return crossings
