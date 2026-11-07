"""`vulnerable` 묶음: P8 안 마을에 있는 취약시설(R-03), 시각 칸은 그 마을을 따른다.

대피명령이 마을 단위(p.76)라서 시설 점이 아니라 **그 마을의 구간 · 시각 · 밤**을 쓴다(11/4).
무엇을 할지(즉시 대피 · 이송 / 사전대피 · 이송 준비)는 LLM이 p.75 · 79 · 89 · 95로 정한다
(11/7 — 옛 R-14 `action`은 엔진에서 뺌). 정원 · 현원은 넘기지 않는다(자료 기준일 숫자, 11/4).
P8 밖 「인근」 시설은 넣지 않는다(거리 기준이 없음 — S2 범위 밖 최근접을 뺀 것과 같은 이유, 11/7).
소관 부처(보건복지부)는 매뉴얼의 기관별 임무라 넣지 않는다 — LLM이 p.89 · 95 청크로 쓴다(11/7).
"""

from typing import Any

from rules.bundles.common import timing
from rules.context import Context
from rules.core.convert import Zone

UNKNOWN_FIXED = [
    {"field": "정원 · 현원", "reason": "자료 기준일 숫자라 넘기지 않음"},
    {"field": "필요한 이송 차량 수", "reason": "산정 기준 없음"},
]


def vulnerable_bundle(ctx: Context) -> dict[str, Any]:
    """`vulnerable`: 걸친 마을 안 취약시설.

    1. 시설의 리(`village_id`)가 P8 안 마을이면 대상(공간 계산 없음)
    2. 시설 줄 = 이름 · 종류 · 리 · 읍면 + 그 마을의 시각 칸 + 낮에만 운영
    3. 마을이 닿는 순 → 리 이름 → 시설 이름 순으로 놓는다
    4. 개수: 5시간 안(이미 닿음 + 위험구역) · 잠재 위험구역 · 낮에만 운영
    5. 종류를 모르는 시설은 `unknown`에 이유와 함께 적는다
    """
    by_id = ctx.village_by_id
    rows = []
    unknown = [dict(u) for u in UNKNOWN_FIXED]
    for f in ctx.inputs.vulnerable:
        reached = by_id.get(f.village_id)  # 1
        if reached is None:
            continue
        v = reached.village
        rows.append(  # 2
            {
                "id": f.id,
                "name": f.name,
                "kind": f.kind,
                "village": v.name,
                "emd": v.emd,
                **timing(ctx, reached.p),
                "daytime_only": f.daytime_only,
            }
        )
        if f.kind is None:  # 5
            unknown.append({"field": f"{f.name} 종류", "reason": "자료 없음"})
    rows.sort(key=lambda r: (r["first_p"], r["village"], r["name"]))  # 3
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
