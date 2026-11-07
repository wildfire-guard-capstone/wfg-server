"""`spread` 묶음: 주 확산 방향(L-08)과 다음에 닿는 마을.

방향 = 현재 화선 중심 → P5 중심(11/7). 「화선 전방(방향에 있는) 마을」은 각도 기준이 필요한
D 값이라 쓰지 않고, 이미 있는 값으로 **다음에 닿는 마을**(아직 안 닿은 마을 중 처음 닿는 P가
가장 작은 곳)을 낸다.
"""

from typing import Any

from rules.bundles.common import timing
from rules.context import Context
from rules.core.convert import round1
from rules.core.direction import azimuth, compass8


def spread_bundle(ctx: Context) -> dict[str, Any]:
    """`spread`: 방향 · 이동 거리 · 다음에 닿는 마을.

    1. 시작 = 현재 화선 중심(화선이 없으면 발화점), 끝 = P5 중심
    2. 방향 = 시작 → 끝 방위각의 8방위, 이동 거리(km). 중심이 그대로면 방향은 모름
    3. 다음에 닿는 마을 = 처음 닿는 P가 1 이상인 마을 중 가장 작은 P의 마을들
    """
    p0, p5 = ctx.inputs.ranges[0], ctx.inputs.ranges[5]
    start, end = p0.centroid, p5.centroid  # 1
    dist = start.distance(end)
    unknown = []
    if dist == 0:  # 2
        unknown.append({"field": "주 확산 방향", "reason": "현재 화선 중심과 P5 중심이 같음"})
    ahead = [r for r in ctx.villages if r.p >= 1]  # 3
    first = min((r.p for r in ahead), default=None)
    next_villages = [
        {"name": r.village.name, "emd": r.village.emd, **timing(ctx, r.p)}
        for r in ahead
        if r.p == first
    ]
    return {
        "dir": None if dist == 0 else compass8(azimuth(start, end)),
        "from": "발화점" if p0.area == 0 else "현재 화선 중심",
        "to": "P5 중심",
        "km": round1(dist / 1000),
        "next_villages": next_villages,
        "unknown": unknown,
    }
