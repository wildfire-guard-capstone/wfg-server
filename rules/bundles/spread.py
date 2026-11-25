"""`spread` 묶음: 주 확산 방향(L-08)과 다음에 닿는 마을.

방향 = 현재 화선 중심 → P5 중심(11/7). 「화선 전방(방향에 있는) 마을」은 각도 기준이 필요한
D 값이라 쓰지 않고, 이미 있는 값으로 **다음에 닿는 마을**(아직 안 닿은 마을 중 처음 닿는 P가
가장 작은 곳)을 낸다.

11/19 평가 R3: P5 중 현재 화선과 이어지지 않은 조각(따로 난 불 — 의성에서는 14:39 안계면 양곡리
발화)이 중심을 끌어 방향이 뒤집혔다. 방향은 **현재 화선에서 자라는 조각**만으로 잰다. 따로 난
불 조각은 면적과 함께 `separate`에 남긴다(마을 판정에는 그대로 들어간다).
"""

from typing import Any

from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from rules.bundles.common import village_timing
from rules.context import Context
from rules.core.convert import round1
from rules.core.direction import azimuth, compass8


def _parts(g: BaseGeometry) -> list[BaseGeometry]:
    return list(getattr(g, "geoms", [g]))


def main_fire(p0: BaseGeometry, p5: BaseGeometry) -> tuple[BaseGeometry, list[BaseGeometry]]:
    """P5를 현재 화선에서 자라는 조각과 따로 난 불 조각으로 나눈다.

    1. P5 조각 중 현재 화선(P0, 발화점이면 그 점)과 닿는 조각 = 본 화선
    2. 닿는 조각이 없으면(입력 오류) P5 전체를 본 화선으로 본다
    """
    parts = _parts(p5)
    main = [g for g in parts if g.intersects(p0)]  # 1
    if not main:  # 2
        return p5, []
    other = [g for g in parts if not g.intersects(p0)]
    return unary_union(main), other


def spread_bundle(ctx: Context) -> dict[str, Any]:
    """`spread`: 방향 · 이동 거리 · 따로 난 불 · 다음에 닿는 마을.

    1. 시작 = 현재 화선 중심(화선이 없으면 발화점), 끝 = P5 중 현재 화선에서 자라는 조각의 중심
    2. 방향 = 시작 → 끝 방위각의 8방위, 중심 이동 거리(km). 중심이 그대로면 방향은 모름.
       P5가 P0를 품으므로 방향 = 「새로 탈 땅(P5 − P0)의 중심」 쪽. km는 불이 번지는 거리가
       아니다(이미 탄 면적이 클수록 작아짐) — 항목에는 넘기지 않는다. 정의 검증은 P1-7
    3. 따로 난 불 조각(P5에서 현재 화선과 닿지 않는 조각) 수 · 면적
    4. 다음에 닿는 마을 = 판정 P가 1 이상인 마을 중 가장 작은 P의 마을들(대피 대상 행정리와 함께)
    """
    p0, p5 = ctx.inputs.ranges[0], ctx.inputs.ranges[5]
    main, other = main_fire(p0, p5)
    start, end = p0.centroid, main.centroid  # 1
    dist = start.distance(end)
    unknown = []
    if dist == 0:  # 2
        unknown.append({"field": "주 확산 방향", "reason": "현재 화선 중심과 P5 중심이 같음"})
    ahead = [r for r in ctx.villages if r.p >= 1]  # 4
    first = min((r.p for r in ahead), default=None)
    next_villages = []
    for r in ahead:
        if r.p != first:
            continue
        admin = [a.admin.name for a in r.reached_admin]
        next_villages.append(
            {
                "name": r.village.name,
                "admin_villages": admin,
                "emd": r.village.emd,
                **village_timing(ctx, r),
            }
        )
    return {
        "dir": None if dist == 0 else compass8(azimuth(start, end)),
        "from": "발화점" if p0.area == 0 else "현재 화선 중심",
        "to": "P5 중 현재 화선에서 자라는 조각의 중심",
        "km": round1(dist / 1000),
        "separate": {  # 3
            "count": len(other),
            "ha_p5": round1(sum(g.area for g in other) / 1e4),
        },
        "next_villages": next_villages,
        "unknown": unknown,
    }
