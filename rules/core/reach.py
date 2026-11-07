"""R-04 처음 닿는 P: 대상이 P0 ~ P8 중 처음 겹치는 범위 번호를 찾는다.

범위는 누적이라(P0 ⊂ P1 ⊂ … ⊂ P8) 한 번 겹치면 뒤도 모두 겹친다 → 반씩 나눠 찾는다.
P0가 발화점(점)이면 겹친 면적이 0이라 「닿는가」로만 본다(R-01).
"""

from collections.abc import Sequence

from shapely.geometry.base import BaseGeometry

MAX_P = 8


def touches(target: BaseGeometry, area: BaseGeometry) -> bool:
    """대상이 범위와 겹치나.

    1. 범위가 점(발화점)이면 닿기만 하면 겹침
    2. 대상이 면이면 겹친 면적 > 0(경계선만 맞닿으면 아님)
    3. 대상이 선이면 겹친 길이 > 0
    4. 대상이 점이면 범위 안(경계 포함)
    """
    if not target.intersects(area):
        return False
    if area.area == 0:  # 1
        return True
    if target.area > 0:  # 2
        return target.intersection(area).area > 0
    if target.length > 0:  # 3
        return target.intersection(area).length > 0
    return True  # 4


def first_p(target: BaseGeometry, ranges: Sequence[BaseGeometry], start: int = 0) -> int | None:
    """대상이 처음 겹치는 P 번호(0 ~ 8). P8과도 안 겹치면 None.

    `start` = 부모(읍면 → 리 → 시설)의 P 번호. 자식은 부모보다 먼저 닿을 수 없다.

    1. P8과 안 겹치면 None
    2. [start, 8]을 반씩 나눠, 겹치는 가장 작은 n을 찾는다(최대 4번)
    """
    if not touches(target, ranges[MAX_P]):  # 1
        return None
    lo, hi = start, MAX_P  # 2
    while lo < hi:
        mid = (lo + hi) // 2
        if touches(target, ranges[mid]):
            hi = mid
        else:
            lo = mid + 1
    return lo
