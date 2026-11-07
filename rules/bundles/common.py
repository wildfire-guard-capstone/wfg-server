"""묶음 공통: 여러 묶음이 함께 쓰는 시각 칸 · 거리 · 이름 찾기."""

from collections.abc import Sequence
from typing import Any

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge

from rules.context import Context
from rules.core.convert import p_from, p_time, p_until, round1, zone_of
from rules.core.places import Place
from rules.core.sun import is_night

MAX_P = 8


def timing(ctx: Context, p: int) -> dict[str, Any]:
    """처음 닿는 P 하나로 시각 칸을 만든다(마을 · 시설 · 도로가 같은 모양을 쓴다).

    1. 구간(J1): 0 이미 닿음 · 1 ~ 5 위험구역 · 6 ~ 8 잠재 위험구역
    2. 닿을 수 있는 구간: `from` = 0시 + (n − 1)시간 · `until` = 0시 + n시간(P0 = 「지금」)
    3. `passed` = 구간 끝이 생성 시각보다 이름(정정 버전의 「예상 시각 경과」, 11/7)
    4. `night` = 구간의 시작이나 끝이 밤(J2, 11/7). 이미 닿았으면 0시로 본다
    """
    t = p_time(ctx.base_at, p)
    night = is_night(t, *ctx.site) or (p > 0 and is_night(p_time(ctx.base_at, p - 1), *ctx.site))
    return {
        "first_p": p,
        "zone": zone_of(p).value,  # 1
        "from": p_from(ctx.base_at, p),  # 2
        "until": p_until(ctx.base_at, p),
        "passed": p > 0 and t < ctx.generated_at,  # 3
        "night": night,  # 4
    }


def km(a: BaseGeometry, b: BaseGeometry) -> float:
    """두 도형 사이 직선거리(km, 소수 한 자리). 면이면 가장 가까운 경계까지."""
    return round1(a.distance(b) / 1000)


def nearest(target: BaseGeometry, places: Sequence[Place], kind: str) -> Place | None:
    """종류가 `kind`인 시설 중 대상에서 가장 가까운 곳. 없으면 None."""
    pool = [p for p in places if p.kind == kind]
    return min(pool, key=lambda p: p.geom.distance(target), default=None)


def village_names(ctx: Context) -> dict[int, tuple[str, str]]:
    """리 id → (리 이름, 읍면). 시설이 든 리 이름을 찾을 때(P8 밖 리도 포함)."""
    return {v.id: (v.name, v.emd) for v in ctx.inputs.villages}


def village_at(ctx: Context, point: BaseGeometry) -> tuple[str, str] | None:
    """점이 든 리(이름, 읍면). 경계 위면 먼저 찾은 리, 어느 리에도 없으면 None."""
    for v in ctx.inputs.villages:
        if v.geom.intersects(point):
            return v.name, v.emd
    return None


def line_ends(part: BaseGeometry, line: BaseGeometry) -> tuple[Point, Point]:
    """걸린 부분의 가장 바깥 두 끝점(선을 따라 잰 위치로 고름). 두 덩어리여도 바깥 끝으로."""
    merged = linemerge(part) if part.geom_type == "MultiLineString" else part
    pieces = getattr(merged, "geoms", [merged])
    points = [Point(c) for g in pieces for c in (g.coords[0], g.coords[-1])]
    points.sort(key=line.project)
    return points[0], points[-1]
