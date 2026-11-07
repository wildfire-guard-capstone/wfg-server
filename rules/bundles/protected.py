"""`protected` 묶음: 8시간 안 송전선 · 변전소 · 전통사찰 · 국가유산(R-03 · R-04, 11/7 확정).

사람이 없는 대상이라 마을 단계가 아니라 **자기 위치가 처음 닿는 P**를 쓴다. 어느 기관에
무엇을 요청할지는 LLM이 근거 청크(p.79 · p.95)로 쓴다(11/7 — S2는 기관을 넘기지 않음).
문화유산 점에는 가장 가까운 경찰서 이름을 사실로 붙인다(S6 · E6 표가 씀).
"""

from typing import Any

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge

from rules.bundles.common import km, nearest, timing, village_at, village_names
from rules.context import Context
from rules.core.convert import round1
from rules.core.places import Place, PowerLine
from rules.core.reach import first_p

EXCLUDED_SUBSTATION = (
    "태양광",
)  # 국가기간산업시설로 보기 어려움(11/5, 의성 발전소 12곳이 모두 태양광)
UNKNOWN_FIXED = [
    {"field": "군사시설", "reason": "위치 정보 없음"},
    {"field": "중요 산림자원", "reason": "위치 정보 없음"},
]


def _ends(part: BaseGeometry, line: BaseGeometry) -> tuple[Point, Point]:
    """걸린 부분의 가장 바깥 두 끝점(선을 따라 잰 위치로 고름). 두 덩어리여도 바깥 끝으로."""
    merged = linemerge(part) if part.geom_type == "MultiLineString" else part
    pieces = getattr(merged, "geoms", [merged])
    points = [Point(c) for g in pieces for c in (g.coords[0], g.coords[-1])]
    points.sort(key=line.project)
    return points[0], points[-1]


def _end_village(ctx: Context, point: Point) -> str:
    """끝점이 든 리. 리 경계 틈에 걸리면 가장 가까운 리."""
    hit = village_at(ctx, point)
    if hit is not None:
        return hit[0]
    return min(ctx.inputs.villages, key=lambda v: v.geom.distance(point)).name


def _touched(ctx: Context, part: BaseGeometry) -> set[str]:
    """선 부분이 지나는(겹친 길이 > 0) 리 이름."""
    if part.is_empty:
        return set()
    return {v.name for v in ctx.inputs.villages if v.geom.intersection(part).length > 0}


def _power(ctx: Context, line: PowerLine, p: int) -> dict[str, Any]:
    """송전선 한 줄(11/7 송전선 규칙).

    1. 5시간 안 부분 · 8시간 안 부분 길이(km)
    2. 이름 = 「{전압} kV 송전선 {끝 리}~{끝 리} 구간」. 끝 리 = 걸린 부분의 가장 바깥 두 끝점이
       든 리(5시간에 걸리면 5시간 부분, 아니면 8시간 부분)
    3. 8시간에서 새로 걸리는 리 = 8시간 부분이 지나는 리 중 5시간 부분이 지나지 않는 리
    """
    p5, p8 = ctx.inputs.ranges[5], ctx.inputs.ranges[8]
    part5, part8 = line.geom.intersection(p5), line.geom.intersection(p8)  # 1
    named = part5 if part5.length > 0 else part8  # 2
    a, b = (_end_village(ctx, pt) for pt in _ends(named, line.geom))
    span = a if a == b else f"{a}~{b}"
    adds = sorted(_touched(ctx, part8) - _touched(ctx, part5))  # 3
    return {
        "id": line.id,
        "name": f"{line.voltage_kv} kV 송전선 {span} 구간",
        "voltage_kv": line.voltage_kv,
        **timing(ctx, p),
        "km_p5": round1(part5.length / 1000),
        "km_p8": round1(part8.length / 1000),
        "p8_adds": adds,
    }


def _point(ctx: Context, f: Place, p: int, with_police: bool) -> dict[str, Any]:
    """점 시설 한 줄: 이름 · 종류 · 리 · 읍면 + 자기 위치의 시각 칸(+ 가장 가까운 경찰서)."""
    names = village_names(ctx)
    village, emd = names.get(f.village_id) or village_at(ctx, f.geom) or (None, None)
    row = {"id": f.id, "name": f.name, "kind": f.kind, "village": village, "emd": emd}
    row.update(timing(ctx, p))
    if with_police:
        station = nearest(f.geom, ctx.inputs.police, "경찰서")
        row["police"] = (
            None if station is None else {"name": station.name, "km": km(f.geom, station.geom)}
        )
    return row


def _reached_points(ctx: Context, places, with_police: bool = False) -> list[dict[str, Any]]:
    """8시간 안에 드는 점 시설만, 닿는 순 → 이름 순."""
    rows = []
    for f in places:
        p = first_p(f.geom, ctx.inputs.ranges)
        if p is not None:
            rows.append(_point(ctx, f, p, with_police))
    return sorted(rows, key=lambda r: (r["first_p"], r["name"]))


def protected_bundle(ctx: Context) -> dict[str, Any]:
    """`protected`: 8시간 안 송전선 · 변전소 · 전통사찰 · 국가유산.

    1. 송전선: 처음 닿는 P(걸친 길이 > 0) → 송전선 규칙
    2. 변전소 · 발전소: 태양광은 뺌 → 점 시설 규칙
    3. 전통사찰 · 국가유산: 점 시설 규칙 + 가장 가까운 경찰서. 국가유산은 전체 수 · 8시간 안 수
    4. 군사시설 · 중요 산림자원은 위치 정보가 없어 `unknown`
       (사찰이 있으면 소장 문화유산의 지정 여부도)
    """
    inp = ctx.inputs
    lines = []
    for line in inp.power_lines:  # 1
        p = first_p(line.geom, inp.ranges)
        if p is not None:
            lines.append(_power(ctx, line, p))
    lines.sort(key=lambda r: (r["first_p"], r["name"]))
    subs = [s for s in inp.substations if s.kind not in EXCLUDED_SUBSTATION]  # 2
    temples = _reached_points(ctx, inp.temples, with_police=True)  # 3
    heritage = _reached_points(ctx, inp.heritage, with_police=True)
    unknown = [dict(u) for u in UNKNOWN_FIXED]  # 4
    if temples:
        unknown.append({"field": "사찰 소장 문화유산의 지정 여부", "reason": "정보 없음"})
    return {
        "power_lines": lines,
        "substations": _reached_points(ctx, subs),
        "temples": temples,
        "heritage": {"total": len(inp.heritage), "in_p8": len(heritage), "items": heritage},
        "basis": ["SM-p077", "SM-p079", "SM-p095"],
        "unknown": unknown,
    }
