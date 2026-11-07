"""R-13 합계: 범위 안 「주택 등 주요시설」 동수를 센다.

주택 등 주요시설 = 주택 + 국가유산(시행령 [별표 3] 비고 4). 「피해 우려」 = P5 안(11/3).
주택은 GIS건물통합정보에서 주용도가 단독 · 공동주택인 건물만 있어 늘 「최소」 동수다.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import shapely
from shapely.geometry.base import BaseGeometry

# 점 모음 = (x 목록, y 목록), EPSG:5179. 주택 2만여 점을 한 번에 세려고 좌표 배열로 받는다.
Points = tuple[Sequence[float], Sequence[float]]


def count_in(area: BaseGeometry, points: Points) -> int:
    """범위 안에 든 점의 개수를 센다.

    1. 점마다 범위와 닿는지 본다(경계선 위 점도 셈, 11/7)
    2. 닿은 점의 개수
    """
    xs, ys = points
    if len(xs) == 0:
        return 0
    return int(shapely.intersects_xy(area, xs, ys).sum())  # 1 · 2


@dataclass(frozen=True)
class FacilityCount:
    houses: int
    heritage: int
    is_minimum: bool = True  # 주용도 확인된 주택만 셈

    @property
    def total(self) -> int:
        return self.houses + self.heritage


def count_facilities(p5: BaseGeometry, houses: Points, heritage: Points) -> FacilityCount:
    """P5 안 주택 등 주요시설 동수(시설피해 우려, 대응단계 요소)를 센다.

    1. P5 안 주택 점 수
    2. P5 안 국가유산 점 수
    3. 둘을 합친 값이 대응단계의 시설 요소(`total`)
    """
    return FacilityCount(houses=count_in(p5, houses), heritage=count_in(p5, heritage))
