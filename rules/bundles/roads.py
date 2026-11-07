"""`roads` 묶음: 불이 닿았거나 5시간 안에 닿을 큰 도로 구간(R-19).

엔진은 「어느 도로의 어느 구간에 언제 불이 닿나」와 관할 경찰서 · 관리기관(사실)까지만 낸다.
누구에게 무엇을 요청할지는 LLM이 p.79 · 83 · 95로 쓴다. 정렬은 닿는 순(순위 아님).
"""

from typing import Any

from shapely.ops import linemerge, unary_union

from rules.bundles.common import km, line_ends, nearest, timing, village_at
from rules.context import Context
from rules.core.convert import round1
from rules.core.places import RoadPiece
from rules.core.reach import first_p, touches

MAJOR = ("motorway", "trunk", "primary", "secondary", "tertiary")  # 큰 도로만(D, OSM 품질)
# 도로 등급 → 관리기관(관리 사실). 고속도로만 분명하다 — 국도 · 지방도는 노선 등급 자료가 필요
MANAGER = {"motorway": "한국도로공사"}


def _grade(piece: RoadPiece) -> str:
    return piece.grade.removesuffix("_link")


def _road_name(piece: RoadPiece) -> str:
    """구간 이름 앞부분: 고속도로 = 도로명, 그 밖 = 「번호호선(도로명)」, 번호가 없으면 도로명."""
    if _grade(piece) == "motorway" and piece.name:
        return piece.name
    if piece.ref and piece.name:
        return f"{piece.ref}호선({piece.name})"
    return f"{piece.ref}호선" if piece.ref else (piece.name or "이름 없는 도로")


def _ends_name(ctx: Context, part, motorway: bool) -> tuple[str, bool]:
    """구간 양 끝 이름. 고속도로는 가장 가까운 나들목 · 분기점, 그 밖은 끝점이 든 리.
    상행 · 하행이 같은 이름이 되도록 끝점을 서 → 동(같으면 남 → 북) 순으로 놓는다.

    고속도로인데 나들목 자료가 없으면 리 이름으로 쓰고 False(모름)를 함께 돌려준다.
    """
    a, b = sorted(line_ends(part, part), key=lambda pt: (pt.x, pt.y))  # 방향과 상관없이 서 → 동
    if motorway and ctx.inputs.junctions:
        names = [min(ctx.inputs.junctions, key=lambda j: j.geom.distance(pt)).name for pt in (a, b)]
        known = True
    else:
        names = [(village_at(ctx, pt) or ("?", ""))[0] for pt in (a, b)]
        known = not motorway
    return (names[0] if names[0] == names[1] else f"{names[0]}~{names[1]}"), known


def roads_bundle(ctx: Context) -> dict[str, Any]:
    """`roads`: P0 ~ P5 안 큰 도로 구간.

    1. 큰 도로 조각 중 P5와 겹친 길이 > 0인 것
    2. 같은 노선(번호, 없으면 도로명)끼리 P5 안 부분을 합치고, 이어진 덩어리 하나 = 구간 하나
    3. 구간마다 처음 닿는 P · 시각 칸 · 범위 안 길이 · 이름(노선 + 양 끝)
    4. 가장 가까운 경찰서(관서만) · 관리기관(고속도로 = 한국도로공사)
    5. 노선과 양 끝 이름이 같은 구간(상행 · 하행)은 하나로 합친다(이른 P, 긴 길이)
    """
    p5 = ctx.inputs.ranges[5]
    groups: dict[str, list[RoadPiece]] = {}
    for piece in ctx.inputs.roads:  # 1
        if _grade(piece) in MAJOR and touches(piece.geom, p5):
            groups.setdefault(piece.ref or piece.name or f"id:{piece.id}", []).append(piece)
    sections: dict[tuple[str, str], dict[str, Any]] = {}
    missing_junctions = False
    for pieces in groups.values():
        merged = linemerge(unary_union([pc.geom.intersection(p5) for pc in pieces]))  # 2
        head = pieces[0]
        motorway = _grade(head) == "motorway"
        for part in getattr(merged, "geoms", [merged]):
            if part.length == 0:
                continue
            p = first_p(part, ctx.inputs.ranges)  # 3
            ends, known = _ends_name(ctx, part, motorway)
            missing_junctions |= not known
            station = nearest(part, ctx.inputs.police, "경찰서")  # 4
            row = {
                "road": _road_name(head),
                "ref": head.ref,
                "grade": _grade(head),
                "section": ends,
                **timing(ctx, p),
                "km": round1(part.length / 1000),
                "police": None
                if station is None
                else {"name": station.name, "km": km(part, station.geom)},
                "manager": MANAGER.get(_grade(head)),
            }
            key = (row["road"], row["section"])  # 5
            old = sections.get(key)
            if old is None or row["first_p"] < old["first_p"]:
                row["km"] = max(row["km"], old["km"]) if old else row["km"]
                sections[key] = row
            else:
                old["km"] = max(old["km"], row["km"])
    rows = sorted(sections.values(), key=lambda r: (r["first_p"], r["road"], r["section"]))
    unknown = []
    if any(r["manager"] is None for r in rows):
        unknown.append({"field": "국도 · 지방도 관리기관", "reason": "노선 등급 자료 없음"})
    if missing_junctions:
        unknown.append({"field": "고속도로 나들목 이름", "reason": "자료 없음(리 이름으로 씀)"})
    return {"sections": rows, "basis": ["SM-p079", "SM-p083"], "unknown": unknown}
