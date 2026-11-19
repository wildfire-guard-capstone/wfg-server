"""`villages` 묶음: P8 안 마을을 구간별로(J1 · J2), 현장 입력 · 합계, 경계만 걸친 마을.

마을 = 법정리 한 줄(11/19 사용자 결정 D2). 구간은 리 안 주택이 처음 닿는 P로 정하고(D1),
닿는 행정리 이름을 붙인다. 행정리 인구는 공식 자료가 없어 넘기지 않는다(인구는 법정리 값).
"""

from dataclasses import asdict
from typing import Any

from rules.bundles.common import timing
from rules.context import Context
from rules.core.convert import Zone, zone_of
from rules.core.villages import EdgeOnly, Reached, VillageField

P5, P8 = 5, 8


def admin_rows(ctx: Context, reached: Reached) -> list[dict[str, Any]]:
    """닿는 행정리: 이름 · 구간 · 구역 주택 수 · 회관. 행정리로 나눠 볼 수 없으면 빈 목록."""
    return [
        {
            "id": a.admin.id,
            "name": a.admin.name,
            "zone": zone_of(a.p).value,
            "first_p": a.p,
            "houses": a.houses,
            "hall": a.admin.hall_name,
        }
        for a in reached.reached_admin
        if a.p is not None
    ]


def _houses(reached: Reached) -> dict[str, Any]:
    c = reached.houses_by_p
    return {
        "total": reached.houses,
        "p5": c[P5] if c else None,
        "p8": c[P8] if c else None,
        "by": "주택" if reached.by_houses else "리 경계(리 안 주택 자료 없음)",
    }


def _village(ctx: Context, reached: Reached) -> dict[str, Any]:
    """마을 한 줄: 이름 · 읍면 · 시각 칸(구간 · from ~ until · 경과 · 밤) · 인구 · 주택 · 행정리 ·
    현장 입력."""
    v = reached.village
    return {
        "id": v.id,
        "name": v.name,
        "emd": v.emd,
        "sigungu": v.sigungu,
        **timing(ctx, reached.p),
        "boundary_p": reached.boundary_p,
        "population": v.population,
        "pop65_est": v.pop65_est,
        "houses": _houses(reached),
        "admin_villages": admin_rows(ctx, reached),
        "field": asdict(ctx.field.get(v.id, VillageField())),
    }


def _edge(ctx: Context, e: EdgeOnly) -> dict[str, Any]:
    v = e.village
    return {
        "id": v.id,
        "name": v.name,
        "emd": v.emd,
        "sigungu": v.sigungu,
        "boundary_p": e.boundary_p,
        "boundary_zone": zone_of(e.boundary_p).value,
        "houses": e.houses,
        "population": v.population,
        "field": asdict(ctx.field.get(v.id, VillageField())),
    }


def _total(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"count": len(rows), "population": sum(r["population"] for r in rows)}


def _unknown(ctx: Context) -> list[dict[str, str]]:
    """모르는 칸: 장애인 · 어린이 인원 + 행정리로 나뉘었는데 구역 자료가 없는 리."""
    unknown = [{"field": "장애인 · 어린이 인원", "reason": "리 단위 자료 없음"}]
    for r in ctx.villages:
        if len(r.admin) >= 2 and not r.zoned:
            field = f"{r.village.name} 행정리 {len(r.admin)}곳 중 불이 닿는 곳"
            unknown.append({"field": field, "reason": "마을 구역(회관 위치) 자료 없음"})
    return unknown


def villages_bundle(ctx: Context) -> dict[str, Any]:
    """`villages`: P8 안 마을을 구간별로(J1). 닿는 순, 같으면 이름 순.

    1. 마을마다 처음 닿는 P(주택 기준) · 구간 시작 · 끝 시각 · 경과 · 밤 · 인구 · 주택 ·
       닿는 행정리 · 현장 입력을 붙인다
    2. 구간으로 나눈다: burning(P0) · p5(P1 ~ P5) · p8(P6 ~ P8)
    3. 구간별 · 5시간 안(burning + p5) 마을 수 · 인구 합계
    4. 경계만 걸친 마을(8시간 안 주택 없음)은 `edge`에 따로 — 대피 대상이 아니다
    """
    groups: dict[Zone, list[dict[str, Any]]] = {z: [] for z in Zone}
    for r in ctx.villages:
        groups[zone_of(r.p)].append(_village(ctx, r))  # 1 · 2
    burning, p5, p8 = groups[Zone.BURNING], groups[Zone.P5], groups[Zone.P8]
    edge = [_edge(ctx, e) for e in ctx.edge_villages]  # 4
    return {
        "burning": burning,
        "p5": p5,
        "p8": p8,
        "edge": edge,
        "totals": {  # 3
            "burning": _total(burning),
            "p5": _total(p5),
            "within_p5": _total(burning + p5),
            "p8": _total(p8),
            "edge": _total(edge),
        },
        "unknown": _unknown(ctx),
    }
