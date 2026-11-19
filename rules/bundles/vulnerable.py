"""`vulnerable` 묶음: P8 안 마을에 있는 취약시설(R-03), 시각 칸은 그 마을을 따른다.

대피명령이 마을 단위(p.76)라서 시설 점이 아니라 **그 마을의 구간 · 시각 · 밤**을 쓴다(11/4).
마을 = 시설이 든 행정리 구역(구역이 있을 때) 또는 법정리이고, 그 P는 구역 · 리 경계에
불이 처음 닿는 P다(11/20 D1 — 11/19 R3 때는 주택 기준).
시설 자리가 그보다 먼저 닿으면 시설 자리 P를 쓴다(주택 자료에 없는 시설 건물).
무엇을 할지(즉시 대피 · 이송 / 사전대피 · 이송 준비)는 LLM이 p.75 · 79 · 89 · 95로 정한다
(11/7 — 옛 R-14 `action`은 엔진에서 뺌). 정원 · 현원은 넘기지 않는다(자료 기준일 숫자, 11/4).
P8 밖 「인근」 시설은 넣지 않는다(거리 기준이 없음 — S2 범위 밖 최근접을 뺀 것과 같은 이유, 11/7).
이송을 맡을 기관(보건소장, 11/17)은 요청 표(rules/items/requests.py)가 정하고,
할 일은 LLM이 p.22 · 89 청크로 쓴다.
"""

from typing import Any

from rules.bundles.common import timing, village_at, village_names
from rules.context import Context
from rules.core.convert import Zone
from rules.core.places import Place
from rules.core.reach import first_p

UNKNOWN_FIXED = [
    {"field": "정원 · 현원", "reason": "자료 기준일 숫자라 넘기지 않음"},
    {"field": "필요한 이송 차량 수", "reason": "산정 기준 없음"},
]


def facility_p(ctx: Context, f: Place) -> tuple[int | None, str | None]:
    """시설의 P와 시설이 든 행정리 이름.

    1. 시설이 든 리가 주택 기준으로 닿으면 그 대피 단위(행정리 구역 · 리)의 P
    2. 시설 자리가 범위에 닿으면 그 P
    3. 둘 중 작은 값. 둘 다 없으면 None(대상 아님)
    """
    reached = ctx.village_by_id.get(f.village_id)
    unit, admin = reached.unit_p(f.geom) if reached else (None, None)  # 1
    own = first_p(f.geom, ctx.inputs.ranges)  # 2
    ps = [p for p in (unit, own) if p is not None]
    return (min(ps) if ps else None), (admin.admin.name if admin else None)  # 3


def vulnerable_bundle(ctx: Context) -> dict[str, Any]:
    """`vulnerable`: 걸친 마을 안 취약시설.

    1. 시설의 P = 든 대피 단위(행정리 구역 · 리)의 주택 P와 시설 자리 P 중 작은 값. 없으면 빠짐
    2. 시설 줄 = 이름 · 종류 · 리 · 행정리 · 읍면 + 그 P의 시각 칸 + 낮에만 운영
    3. 마을이 닿는 순 → 리 이름 → 시설 이름 순으로 놓는다
    4. 개수: 5시간 안(이미 닿음 + 위험구역) · 잠재 위험구역 · 낮에만 운영
    5. 종류를 모르는 시설은 `unknown`에 이유와 함께 적는다
    """
    names = village_names(ctx)
    rows = []
    unknown = [dict(u) for u in UNKNOWN_FIXED]
    for f in ctx.inputs.vulnerable:
        p, admin = facility_p(ctx, f)  # 1
        if p is None:
            continue
        village, emd = names.get(f.village_id) or village_at(ctx, f.geom) or (None, None)
        rows.append(  # 2
            {
                "id": f.id,
                "name": f.name,
                "kind": f.kind,
                "village": village,
                "admin_village": admin,
                "emd": emd,
                **timing(ctx, p),
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
