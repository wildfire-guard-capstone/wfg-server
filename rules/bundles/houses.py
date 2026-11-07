"""`houses` 묶음: 5시간 안(P5) 주택, 리별 동수(R-13 확장, 11/7 확정).

주택 = GIS건물통합정보에서 주용도가 단독 · 공동주택인 건물만 → 늘 「최소」 동수다.
범위 밖 「100 m 안 주택」은 넣지 않는다(매뉴얼 근거 없는 D 값, 11/7).
"""

from typing import Any

from rules.context import Context
from rules.core.facility import count_in


def houses_bundle(ctx: Context) -> dict[str, Any]:
    """`houses`: P5 안 주택.

    1. 합계 = P5 안 주택 점 수(대응단계 시설 요소와 같은 값, R-13)
    2. 리별 = 걸친 리마다 (리 ∩ P5) 안 주택 점 수. 0동인 리는 뺀다. 마을이 닿는 순
    """
    p5 = ctx.inputs.ranges[5]
    by_village = []
    for r in ctx.villages:  # 2
        n = count_in(r.village.geom.intersection(p5), ctx.inputs.houses)
        if n:
            by_village.append({"village": r.village.name, "emd": r.village.emd, "houses": n})
    return {
        "is_minimum": ctx.facility.is_minimum,
        "within_p5": {"total": ctx.facility.houses, "by_village": by_village},  # 1
        "unknown": [],
    }
