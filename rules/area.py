"""R-15 면적: 이미 잰 면적으로 하는 계산(도형은 다루지 않음)."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


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
