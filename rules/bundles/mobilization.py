"""`mobilization` 묶음: 현재 · P5 단계의 진화자원 동원 범위(J7)와 범위 안 기지(R-18).

근거: 산림청고시 제2026-94호 [별표] 「산불 대응 단계별 진화자원 동원 · 협조 기준」.
「100% · 50% 이내」는 부를 수 있는 대수의 상한이라 엔진은 대수를 정하지 않는다. 추가 투입 대수 ·
배치는 기준이 없어 비운다(E). 응원을 요청할지는 LLM이 고시 · p.77 · 78로 쓴다.
"""

from typing import Any

from rules.bundles.common import km
from rules.context import Context
from rules.core.places import Base
from rules.core.stage import LABEL, Stage

AIR_KM = {Stage.INITIAL: 50, Stage.STAGE1: 100, Stage.STAGE2: None}  # None = 전국
BASIS = ["NOTI-MOBL-t0-p1", "NOTI-MOBL-t0-p2"]
UNKNOWN_FIXED = [{"field": "추가 투입 대수와 배치 위치", "reason": "산정 기준 없음"}]


def _scope(ctx: Context, stage: Stage, home: str, sido: str) -> dict[str, Any]:
    """J7: 단계 → 범위. 지상 = 관할 100% + (1단계 인접 시군 50% 이내 · 2단계 시 · 도 100%),
    공중 = 발화지 반경 50 km · 100 km · 전국."""
    if stage is Stage.STAGE1:
        extra = {
            "kind": "neighbors",
            "names": list(ctx.inputs.neighbors.get(home, [])),
            "share": "50% 이내",
        }
    elif stage is Stage.STAGE2:
        extra = {"kind": "sido", "names": [sido], "share": "100%"}
    else:
        extra = {"kind": "none", "names": [], "share": None}
    radius = AIR_KM[stage]
    return {
        "stage": LABEL[stage],
        "ground": {"home": {"name": home, "share": "100%"}, "extra": extra},
        "air": {"kind": "national" if radius is None else "radius", "km": radius},
    }


def _in_scope(ctx: Context, base: Base, scope: dict[str, Any]) -> bool:
    """기지가 범위 안인가: 헬기 기지는 발화지에서 반경 안(전국이면 늘), 지상 기지는 시군 · 시도."""
    if base.kind.endswith("헬기"):
        radius = scope["air"]["km"]
        return radius is None or ctx.inputs.ignition.distance(base.geom) <= radius * 1000
    ground = scope["ground"]
    if base.sigungu == ground["home"]["name"]:
        return True
    extra = ground["extra"]
    if extra["kind"] == "neighbors":
        return base.sigungu in extra["names"]
    return extra["kind"] == "sido" and base.sido in extra["names"]


def mobilization_bundle(ctx: Context) -> dict[str, Any]:
    """`mobilization`: 동원 범위와 범위 안 기지.

    1. 관할 = 현재 화선이 가장 많이 걸친 시 · 군 · 구, 그 시 · 도(R-02)
    2. 현재 · P5 단계(R-17)마다 범위(J7)
    3. 기지마다: 지금 범위 안이면 now, 아니고 P5 범위 안이면 p5, 둘 다 아니면 뺀다
    4. 관할 / 응원 = 기지 관할 구역에 산불 시군구 또는 그 시 · 도가 있나(DB 설계) · 발화지에서
       거리 · 부를 수 있는(대기) 대수 · 이 산불에 이미 투입된(자원 연결) 대수
    5. 발화지에서 가까운 순(고시 비고 ② 「가까운 기관」 — 순위가 아니라 보여 주는 순서)
    """
    home, sido = ctx.span_now.sigungu[0], ctx.span_now.sido[0]  # 1
    now = _scope(ctx, ctx.stage_now.stage, home, sido)  # 2
    p5 = _scope(ctx, ctx.stage_p5.stage, home, sido)
    bases = []
    for b in ctx.inputs.bases:
        first = "now" if _in_scope(ctx, b, now) else ("p5" if _in_scope(ctx, b, p5) else None)  # 3
        if first is None:
            continue
        bases.append(
            {
                "name": b.name,
                "kind": b.kind,
                "sigungu": b.sigungu,
                "jurisdiction": "관할" if {home, sido} & set(b.coverage) else "응원",  # 4
                "first_scope": first,
                "km": km(ctx.inputs.ignition, b.geom),
                "available": {"helicopters": b.helicopters, "vehicles": b.vehicles},
                "deployed": {
                    "helicopters": b.deployed_helicopters,
                    "vehicles": b.deployed_vehicles,
                },
            }
        )
    bases.sort(key=lambda r: (r["km"], r["name"]))  # 5
    unknown = [dict(u) for u in UNKNOWN_FIXED]
    if any(None in r["available"].values() for r in bases):
        unknown.append({"field": "기지별 부를 수 있는 대수 일부", "reason": "자원 상태 자료 없음"})
    return {
        "home": home,
        "now": now,
        "p5": p5,
        "bases": bases,
        "basis": list(BASIS),
        "unknown": unknown,
    }
