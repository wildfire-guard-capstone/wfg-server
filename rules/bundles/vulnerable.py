"""`vulnerable` 묶음: 불에 가까운 취약시설(R-03).

시설 자리가 8시간 뒤 불에서 1 km 안이면 대상이다(`near_p`, 11/25 사용자 결정 — p.75 「인근 취약시설
포함」). 대피하는 마을(행정리 구역, 구역을 모르면 리) 안 시설은 그 마을이 더 이르면 그 구간을
따른다(p.76 대피명령은 마을 단위). 전에는 시설이 든 구역만 보았는데, 행정리 구역은 집 둘레로 추정한
범위라 시설이 구역 밖이기 쉬워(의성 29곳 중 15곳) 불에서 25 m인 요양원이 빠졌다(QA 4-6).
무엇을 할지(즉시 대피 · 이송 / 사전대피 · 이송 준비)는 LLM이 p.75 · 79 · 89 · 95로 정한다
(11/7 — 옛 R-14 `action`은 엔진에서 뺌). 정원 · 현원은 넘기지 않는다(자료 기준일 숫자, 11/4).
이송을 맡을 기관(보건소장, 11/17)은 요청 표(rules/items/requests.py)가 정하고,
할 일은 LLM이 p.22 · 89 청크로 쓴다.
"""

from typing import Any

from shapely.geometry.base import BaseGeometry

from rules.bundles.common import reach_timing, village_at, village_names
from rules.context import Context
from rules.core.convert import Zone
from rules.core.places import Place
from rules.core.villages import AdminReached, near_p

UNKNOWN_FIXED = [
    {"field": "정원 · 현원", "reason": "자료 기준일 숫자라 넘기지 않음"},
    {"field": "필요한 이송 차량 수", "reason": "산정 기준 없음"},
]


def facility_reach(
    ctx: Context, f: Place
) -> tuple[int | None, bool, BaseGeometry, AdminReached | None]:
    """시설의 P · 실제로 닿나 · 거리를 재는 곳 · 시설이 든 행정리.

    1. 시설 자리의 `near_p`
    2. 시설이 든 대피 대상 행정리(구역을 모르는 리는 리)가 더 이르면 그 P
    """
    p, touched = near_p(f.geom, ctx.inputs.ranges)  # 1
    reached = ctx.village_by_id.get(f.village_id)
    if reached is None:
        return p, touched, f.geom, None
    admin = reached.admin_at(f.geom)  # 2
    if admin is not None:
        unit, unit_touched, area = admin.p, admin.touched, admin.admin.geom
    else:
        unit, unit_touched = (None, False) if reached.by_admin else (reached.p, True)
        area = reached.village.geom
    if unit is not None and (p is None or unit < p):
        return unit, unit_touched, area, admin
    return p, touched, f.geom, admin


def vulnerable_bundle(ctx: Context) -> dict[str, Any]:
    """`vulnerable`: 불에 가까운 취약시설.

    1. 시설의 P(자리 · 든 행정리 가운데 이른 쪽). 불에서 멀면 빠짐
    2. 시설 줄 = 이름 · 종류 · 리 · 행정리 · 읍면 + 그 P의 시각 칸 + 낮에만 운영
    3. 닿는 순 → 리 이름 → 시설 이름 순으로 놓는다
    4. 개수: 5시간 안(이미 닿음 + 위험구역) · 잠재 위험구역 · 낮에만 운영
    5. 종류를 모르는 시설은 `unknown`에 이유와 함께 적는다
    """
    names = village_names(ctx)
    rows = []
    unknown = [dict(u) for u in UNKNOWN_FIXED]
    for f in ctx.inputs.vulnerable:
        p, touched, place, admin = facility_reach(ctx, f)  # 1
        if p is None:
            continue
        zoned = admin is not None and ctx.village_by_id[f.village_id].zoned
        village, emd = names.get(f.village_id) or village_at(ctx, f.geom) or (None, None)
        rows.append(  # 2
            {
                "id": f.id,
                "name": f.name,
                "kind": f.kind,
                "village": village,
                "admin_village": admin.admin.name if zoned else None,
                "emd": emd,
                **reach_timing(ctx, p, touched, [place]),
                "daytime_only": f.daytime_only,
            }
        )
        if f.kind is None:  # 5
            unknown.append({"field": f"{f.name} 종류", "reason": "자료 없음"})
    rows.sort(key=lambda r: (r["first_p"], r["village"] or "", r["name"]))  # 3
    within = [r for r in rows if r["zone"] != Zone.P8.value]
    return {
        "in_range": rows,
        "counts": {  # 4
            "within_p5": len(within),
            "p8": len(rows) - len(within),
            "daytime_only": sum(r["daytime_only"] for r in rows),
        },
        "basis": ["SM-p075", "SM-p079", "SM-p084"],
        "unknown": unknown,
    }
