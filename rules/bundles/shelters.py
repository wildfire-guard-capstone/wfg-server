"""`shelters` 묶음: 대피할 마을마다 갈 수 있는 대피소 후보(R-09 ~ R-12, J8).

엔진은 후보만 보여 주고 배정(몇 명을 어디로)은 하지 않는다. 「먼저 모일 곳 = 5 km 안 경로당」은
넣지 않는다(근거 없는 D 값 — 마을 시설 2곳을 거리와 함께 그대로 보여 준다, 11/7 점검).

11/19 평가 R3(사용자 결정 D3):
- 대피소를 빼는 기준에 「대피 대상 안」을 더한다. 대피소가 든 대피 단위(행정리 · 리)가 위험구역이면
  빼고 잠재 위험구역이면 예외로 둔다. 대피하는 마을 안으로 대피시키지 않는다
  (p.79 「산불로부터 안전한 경로 … 대피장소로 선정」). 구역 밖 대피소는 같은 리의 가장 가까운
  행정리를 따르고, 대피 대상이 아닌 행정리 안 대피소는 그대로 쓴다(11/25)
- 거리는 행정리 회관에서 잰다(p.84 · 90 · 96 「행정리별 대피장소 및 비상 집결지」).
  회관을 모르면 주택 밀집 지점
"""

from typing import Any

import numpy as np
import shapely
from shapely.geometry import Point

from rules.bundles.common import km, village_timing
from rules.context import Context
from rules.core.places import Place
from rules.core.reach import first_p
from rules.core.villages import Reached

PER_TIER = 2  # 종류마다 후보 수(실무매뉴얼 「1, 2후보지」를 빌림, B)
UNKNOWN_FIXED = [
    {"field": "대피소 실제 개방 여부와 남은 수용 인원", "reason": "실시간 정보 없음"},
    {"field": "대피소 배정", "reason": "하지 않음(후보만 보여 줌)"},
]
HOUSE_POINT = "주택 밀집 지점"
DENSE_RADIUS_M = (
    300.0  # 주택 밀집 지점: 이 반경 안 같은 리 주택 수가 가장 많은 주택(11/20 사용자 확정)
)


def _dense_point(ctx: Context, area) -> Point | None:
    """영역 안 주택 가운데 반경 `DENSE_RADIUS_M` 안에 같은 영역 주택이 가장 많은 주택 점
    (11/20 사용자 결정 — 「주택 밀집 지점」 정의 ①). 같으면 영역 주택 무게중심에 가까운 점.
    주택이 없으면 None.

    전에는 무게중심에 가장 가까운 주택이었다 — 신월리처럼 주택 무리가 둘이면
    두 무리 사이의 외딴 집이 될 수 있었다."""
    xs, ys = (np.asarray(a, dtype=float) for a in ctx.inputs.houses)
    if len(xs):
        inside = shapely.intersects_xy(area, xs, ys)
        if inside.any():
            hx, hy = xs[inside], ys[inside]
            d2 = (hx[:, None] - hx[None, :]) ** 2 + (hy[:, None] - hy[None, :]) ** 2
            near = (d2 <= DENSE_RADIUS_M**2).sum(axis=1)
            to_center = (hx - hx.mean()) ** 2 + (hy - hy.mean()) ** 2
            i = int(np.lexsort((to_center, -near))[0])  # 이웃 많은 순, 같으면 무게중심 가까운 순
            return Point(hx[i], hy[i])
    return None


def origin(ctx: Context, reached: Reached) -> tuple[Point, dict[str, Any]]:
    """거리를 재는 점과 그 설명.

    1. 행정리로 나눠 볼 수 있으면 불에 가장 먼저 가까워지는 행정리의 회관
    2. 나뉘지 않은 리(행정리 하나)면 그 회관
    3. 회관을 모르면 리 안 주택 밀집 지점, 주택도 없으면 리 대표점(리 영역 안의 한 점)
    """
    near = sorted(reached.reached_admin, key=lambda a: (a.p, not a.touched))
    first = near[:1]  # 1
    single = reached.admin if len(reached.admin) == 1 else ()  # 2
    for a in [*first, *single]:
        if a.admin.hall is not None:
            return a.admin.hall, {"kind": "회관", "name": a.admin.hall_name, "of": a.admin.name}
    at = _dense_point(ctx, reached.village.geom)  # 3
    if at is not None:
        return at, {"kind": HOUSE_POINT, "name": None, "of": reached.village.name}
    rep = reached.village.geom.representative_point()
    return rep, {"kind": "리 대표점", "name": None, "of": reached.village.name}


def safety(p: int | None) -> str:
    """J8 대피소 안전(대피소 점의 구간): P0 ~ P5 → excluded · P6 ~ P8 → exception · 밖 → safe."""
    if p is None:
        return "safe"
    return "excluded" if p <= 5 else "exception"


ORDER = {"safe": 0, "exception": 1, "excluded": 2}
OWN_REASON = {"excluded": "5시간 안에 불이 닿음", "exception": "8시간 안에 불이 닿음"}
UNIT_REASON = {
    "excluded": "대피 대상 마을 안({}, 위험구역)",
    "exception": "사전대피 마을 안({}, 잠재 위험구역)",
}


def judge(ctx: Context, s: Place) -> dict[str, Any]:
    """대피소 하나의 안전 판정: 건물 자리 P와 든 대피 단위 P 중 나쁜 쪽.

    1. 건물 자리가 처음 닿는 P → J8
    2. 대피소가 든 리가 대피 대상이면 든 대피 단위(가장 가까운 행정리 · 리)의 P → J8
    3. 더 나쁜 판정과 그 이유를 남긴다. 같으면 건물 자리 이유
    """
    own = safety(first_p(s.geom, ctx.inputs.ranges))  # 1
    reached = ctx.village_by_id.get(s.village_id)
    unit, unit_name = "safe", None
    if reached is not None:  # 2
        p, admin = reached.unit_p(s.geom)
        unit = safety(p)
        unit_name = admin.admin.name if admin else reached.village.name
    if ORDER[unit] > ORDER[own]:  # 3
        return {"state": unit, "reason": UNIT_REASON[unit].format(unit_name), "by": "unit"}
    return {"state": own, "reason": OWN_REASON.get(own), "by": "own"}


def _site(s: Place, d: float, state: str) -> dict[str, Any]:
    return {"name": s.name, "kind": s.kind, "safety": state, "km": d, "capacity": s.capacity}


def _pick(ctx: Context, at: Point, tier: str, judged: dict[int, dict]) -> tuple[list, list]:
    """한 종류(마을 시설 · 거점 시설)에서 후보를 고른다.

    1. 그 종류 대피소를 거리 재는 점에서 가까운 순으로 놓는다(직선거리)
    2. 안전한 곳 중 가까운 2곳. 안전한 곳이 없으면 예외 중 가까운 2곳
    3. 고른 후보보다 가까운데 빠진 곳은 이유와 함께 `excluded`
    """
    pool = sorted(
        (s for s in ctx.inputs.shelters if s.tier == tier), key=lambda s: s.geom.distance(at)
    )  # 1
    state = {s.id: judged[s.id]["state"] for s in pool}
    safe = [s for s in pool if state[s.id] == "safe"][:PER_TIER]  # 2
    chosen = safe or [s for s in pool if state[s.id] == "exception"][:PER_TIER]
    sites = [_site(s, km(at, s.geom), state[s.id]) for s in chosen]
    if chosen:  # 3
        far = max(s.geom.distance(at) for s in chosen)
        skipped = [s for s in pool if s not in chosen and state[s.id] != "safe"]
        skipped = [s for s in skipped if s.geom.distance(at) < far]
    else:
        skipped = [s for s in pool if state[s.id] != "safe"][:PER_TIER]
    excluded = [{"name": s.name, "reason": judged[s.id]["reason"]} for s in skipped]
    return sites, excluded


def shelters_bundle(ctx: Context) -> dict[str, Any]:
    """`shelters`: P8 안 마을(이미 닿음 · 위험구역 · 잠재 위험구역)마다 대피소 후보.

    1. 대피소마다 안전 판정: 건물 자리 P와 든 대피 단위 P 중 나쁜 쪽(J8 + R3 D3)
    2. 마을마다 거리 재는 점을 정한다(먼저 닿는 행정리 회관 → 주택 밀집 지점)
    3. 마을 시설(경로당) · 거점 시설(학교 · 체육관 등)에서 각각 후보를 고른다. 리 · 읍면 · 시군
       경계로 자르지 않는다(p.76 상호 대피소 사용)
    4. 두 종류 모두 후보가 없으면 `no_options`
    5. 후보에서 빠지거나 예외가 된 대피소 수: 8시간 안(건물 자리) · 대피 대상 마을 안
    """
    judged = {s.id: judge(ctx, s) for s in ctx.inputs.shelters}  # 1
    rows = []
    for r in ctx.villages:
        at, how = origin(ctx, r)  # 2
        village_sites, ex_v = _pick(ctx, at, "village", judged)  # 3
        hub_sites, ex_h = _pick(ctx, at, "hub", judged)
        admin = [a.admin.name for a in r.reached_admin]
        rows.append(
            {
                "village": r.village.name,
                "admin_villages": admin,
                "emd": r.village.emd,
                **village_timing(ctx, r),
                "population": r.population,
                "pop65_est": r.pop65_est,
                "origin": how,
                "village_sites": village_sites,
                "hub_sites": hub_sites,
                "excluded": ex_v + ex_h,
                "no_options": not village_sites and not hub_sites,  # 4
            }
        )
    candidates = [s for s in ctx.inputs.shelters if s.tier is not None]
    out = [judged[s.id] for s in candidates if judged[s.id]["state"] != "safe"]
    return {
        "villages": rows,
        "counts": {
            "villages": len(rows),
            "no_options": sum(r["no_options"] for r in rows),
            "in_p8": sum(j["by"] == "own" for j in out),  # 5
            "in_targets": sum(j["by"] == "unit" for j in out),
        },
        "basis": ["SM-p075", "SM-p079", "SM-p084"],
        "unknown": [dict(u) for u in UNKNOWN_FIXED],
    }
