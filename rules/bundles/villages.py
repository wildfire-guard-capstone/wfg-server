"""`villages` 묶음: P8 안 마을을 구간별로(J1 · J2), 현장 입력 · 합계."""

from dataclasses import asdict
from typing import Any

from rules.bundles.common import timing
from rules.context import Context
from rules.core.convert import Zone, zone_of
from rules.core.villages import Reached, VillageField


def _village(ctx: Context, reached: Reached) -> dict[str, Any]:
    """마을 한 줄: 이름 · 읍면 · 시각 칸(구간 · from ~ until · 경과 · 밤) · 인구 · 현장 입력."""
    v = reached.village
    return {
        "id": v.id,
        "name": v.name,
        "emd": v.emd,
        "sigungu": v.sigungu,
        **timing(ctx, reached.p),
        "population": v.population,
        "pop65_est": v.pop65_est,
        "field": asdict(ctx.field.get(v.id, VillageField())),
    }


def _total(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"count": len(rows), "population": sum(r["population"] for r in rows)}


def villages_bundle(ctx: Context) -> dict[str, Any]:
    """`villages`: P8 안 마을을 구간별로(J1). 닿는 순, 같으면 이름 순.

    1. 마을마다 처음 닿는 P · 구간 시작 · 끝 시각 · 경과 · 밤 · 인구 · 현장 입력을 붙인다
    2. 구간으로 나눈다: burning(P0) · p5(P1 ~ P5) · p8(P6 ~ P8)
    3. 구간별 · 5시간 안(burning + p5) 마을 수 · 인구 합계
    """
    groups: dict[Zone, list[dict[str, Any]]] = {z: [] for z in Zone}
    for r in ctx.villages:
        groups[zone_of(r.p)].append(_village(ctx, r))  # 1 · 2
    burning, p5, p8 = groups[Zone.BURNING], groups[Zone.P5], groups[Zone.P8]
    return {
        "burning": burning,
        "p5": p5,
        "p8": p8,
        "totals": {  # 3
            "burning": _total(burning),
            "p5": _total(p5),
            "within_p5": _total(burning + p5),
            "p8": _total(p8),
        },
        "unknown": [{"field": "장애인 · 어린이 인원", "reason": "리 단위 자료 없음"}],
    }
