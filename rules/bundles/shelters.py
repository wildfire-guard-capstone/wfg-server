"""`shelters` 묶음: 대피할 마을마다 갈 수 있는 대피소 후보(R-09 ~ R-12, J8).

엔진은 후보만 보여 주고 배정(몇 명을 어디로)은 하지 않는다. 「먼저 모일 곳 = 5 km 안 경로당」은
넣지 않는다(근거 없는 D 값 — 마을 시설 2곳을 거리와 함께 그대로 보여 준다, 11/7 점검).
"""

from typing import Any

import numpy as np
import shapely
from shapely.geometry import Point

from rules.bundles.common import km, timing
from rules.context import Context
from rules.core.places import Place
from rules.core.reach import first_p
from rules.core.villages import Village

PER_TIER = 2  # 종류마다 후보 수(실무매뉴얼 「1, 2후보지」를 빌림, B)
UNKNOWN_FIXED = [
    {"field": "대피소 실제 개방 여부와 남은 수용 인원", "reason": "실시간 정보 없음"},
    {"field": "대피소 배정", "reason": "하지 않음(후보만 보여 줌)"},
]


def origin(ctx: Context, village: Village) -> Point:
    """거리를 재는 점(11/7 「주택 밀집 지점」을 기준값 없이): 리 안 주택 점의 중심에 가장 가까운
    주택 점. 리 안에 주택이 없으면 리 대표점(리 영역 안의 한 점)."""
    xs, ys = (np.asarray(a, dtype=float) for a in ctx.inputs.houses)
    if len(xs):
        inside = shapely.intersects_xy(village.geom, xs, ys)
        if inside.any():
            hx, hy = xs[inside], ys[inside]
            i = int(np.argmin((hx - hx.mean()) ** 2 + (hy - hy.mean()) ** 2))
            return Point(hx[i], hy[i])
    return village.geom.representative_point()


def safety(p: int | None) -> str:
    """J8 대피소 안전(대피소 점의 구간): P0 ~ P5 → excluded · P6 ~ P8 → exception · 밖 → safe."""
    if p is None:
        return "safe"
    return "excluded" if p <= 5 else "exception"


REASON = {"excluded": "5시간 안에 불이 닿음", "exception": "8시간 안에 불이 닿음"}


def _site(s: Place, d: float, state: str) -> dict[str, Any]:
    return {"name": s.name, "kind": s.kind, "safety": state, "km": d, "capacity": s.capacity}


def _pick(ctx: Context, at: Point, tier: str, judged: dict[int, str]) -> tuple[list, list]:
    """한 종류(마을 시설 · 거점 시설)에서 후보를 고른다.

    1. 그 종류 대피소를 마을의 거리 재는 점에서 가까운 순으로 놓는다(직선거리)
    2. 안전한 곳 중 가까운 2곳. 안전한 곳이 없으면 8시간 안(예외) 중 가까운 2곳
    3. 고른 후보보다 가까운데 빠진 곳은 이유와 함께 `excluded`
    """
    pool = sorted(
        (s for s in ctx.inputs.shelters if s.tier == tier), key=lambda s: s.geom.distance(at)
    )  # 1
    safe = [s for s in pool if judged[s.id] == "safe"][:PER_TIER]  # 2
    chosen = safe or [s for s in pool if judged[s.id] == "exception"][:PER_TIER]
    sites = [_site(s, km(at, s.geom), judged[s.id]) for s in chosen]
    if chosen:  # 3
        far = max(s.geom.distance(at) for s in chosen)
        skipped = [s for s in pool if s not in chosen and judged[s.id] != "safe"]
        skipped = [s for s in skipped if s.geom.distance(at) < far]
    else:
        skipped = [s for s in pool if judged[s.id] != "safe"][:PER_TIER]
    excluded = [{"name": s.name, "reason": REASON[judged[s.id]]} for s in skipped]
    return sites, excluded


def shelters_bundle(ctx: Context) -> dict[str, Any]:
    """`shelters`: P8 안 마을(이미 닿음 · 위험구역 · 잠재 위험구역)마다 대피소 후보.

    1. 대피소마다 처음 닿는 P → J8 안전 판정(대피소 건물 자리 기준)
    2. 마을마다 거리 재는 점을 정한다(주택 밀집 지점)
    3. 마을 시설(경로당) · 거점 시설(학교 · 체육관 등)에서 각각 후보를 고른다. 리 · 읍면 · 시군
       경계로 자르지 않는다(p.76 상호 대피소 사용)
    4. 두 종류 모두 후보가 없으면 `no_options`
    5. 8시간 안에 든 대피소 수(후보에서 빠지거나 예외가 된 곳)
    """
    judged = {s.id: safety(first_p(s.geom, ctx.inputs.ranges)) for s in ctx.inputs.shelters}  # 1
    rows = []
    for r in ctx.villages:
        at = origin(ctx, r.village)  # 2
        village_sites, ex_v = _pick(ctx, at, "village", judged)  # 3
        hub_sites, ex_h = _pick(ctx, at, "hub", judged)
        rows.append(
            {
                "village": r.village.name,
                "emd": r.village.emd,
                **timing(ctx, r.p),
                "population": r.village.population,
                "pop65_est": r.village.pop65_est,
                "village_sites": village_sites,
                "hub_sites": hub_sites,
                "excluded": ex_v + ex_h,
                "no_options": not village_sites and not hub_sites,  # 4
            }
        )
    candidates = [s for s in ctx.inputs.shelters if s.tier is not None]
    return {
        "villages": rows,
        "counts": {
            "villages": len(rows),
            "no_options": sum(r["no_options"] for r in rows),
            "in_p8": sum(judged[s.id] != "safe" for s in candidates),  # 5
        },
        "basis": ["SM-p075", "SM-p079"],
        "unknown": [dict(u) for u in UNKNOWN_FIXED],
    }
