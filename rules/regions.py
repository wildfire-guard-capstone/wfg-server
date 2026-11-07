"""R-02 걸친 지역: 범위가 어느 시 · 도, 시 · 군 · 구에 걸쳤나.

지금은 지휘권자(L-01)가 쓰는 시 · 도 · 시 · 군 · 구 이름까지만 낸다. 읍면 · 리까지
내려가는 지역 트리는 대피 항목(E)을 만들 때 더한다.
⚠ 아주 조금만 걸친 경우의 최소 겹침 면적은 정하지 않았다 — 지금은 조금이라도 겹치면 걸침.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class Region:
    name: str  # 경상북도, 의성군 …
    geom: BaseGeometry  # 경계, EPSG:5179


def spanned(area: BaseGeometry, regions: Sequence[Region]) -> list[str]:
    """범위가 걸친 지역 이름을 겹친 면적이 큰 순으로 낸다.

    1. 범위와 닿는 지역만 고른다
    2. 범위가 면(화선 · P1 ~ P8)이면 겹친 면적이 0보다 큰 지역만 남긴다
       (경계선만 맞닿은 지역은 뺌)
    3. 범위가 점(발화점)이면 그 점이 닿은 지역을 모두 남긴다
    4. 겹친 면적이 큰 순, 같으면 이름 순으로 놓는다
    """
    hits = []
    for region in regions:
        if not region.geom.intersects(area):  # 1
            continue
        overlap = region.geom.intersection(area).area
        if area.area > 0 and overlap <= 0:  # 2
            continue
        hits.append((overlap, region.name))  # 2 · 3
    hits.sort(key=lambda hit: (-hit[0], hit[1]))  # 4
    return [name for _, name in hits]
