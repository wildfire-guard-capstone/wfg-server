"""`agencies` 묶음: 담당 관서 이름(사실) — 시군구별 관할 소방서 · 경찰서, 상황실, 응급의료기관.

「어느 기관에 무엇을 요청하나」는 매뉴얼 문장이라 엔진이 정하지 않는다 — 항목 쪽 요청 표 틀
(items/requests.py)이 이 이름들과 다른 묶음의 대상을 짝짓는다(11/7 점검).
"""

from typing import Any

from rules.bundles.common import km
from rules.context import Context
from rules.core.regions import spanned

CENTRAL_ROOM = "중앙재난안전상황실"


def _station(ctx: Context, places, kind: str, sigungu: str) -> str | None:
    """그 시군구에 있는 관서(지구대 · 파출소 · 안전센터가 아닌 서 단위) 중 이름 순 첫 곳."""
    names = sorted(p.name for p in places if p.kind == kind and p.sigungu == sigungu)
    return names[0] if names else None


def agencies_bundle(ctx: Context) -> dict[str, Any]:
    """`agencies`: 담당 관서 이름.

    1. P8이 걸친 시군구마다 관할 소방서 · 경찰서(그 시군구 안 서 단위 관서). 없으면 null + 이유
    2. 상황실 = 중앙 · 관할 시 · 도(이름만, 어느 쪽에 요청할지는 고르지 않음)
    3. 응급의료기관 = 응급실이 있는 병원 중 산불 시군과 인접 시군에 있는 곳(D 대신 동원 범위가 이미
       쓰는 이웃), 발화지에서 가까운 순. 지정 등급은 자료가 없어 모름
    """
    inp = ctx.inputs
    unknown = [{"field": "한국전력공사 담당 사업소", "reason": "정보 없음"}]
    rows = []
    for sg in spanned(inp.ranges[8], inp.sigungu):  # 1
        fire = _station(ctx, inp.fire_stations, "소방서", sg)
        police = _station(ctx, inp.police, "경찰서", sg)
        for label, value in (("소방서", fire), ("경찰서", police)):
            if value is None:
                unknown.append({"field": f"{sg} 관할 {label}", "reason": "자료 없음"})
        rows.append({"sigungu": sg, "fire": fire, "police": police})
    home, sido = ctx.span_now.sigungu[0], ctx.span_now.sido[0]
    near = {home, *inp.neighbors.get(home, [])}
    medical = sorted(
        (
            {"name": h.name, "sigungu": h.sigungu, "grade": None, "km": km(inp.ignition, h.geom)}
            for h in inp.hospitals
            if h.emergency and h.sigungu in near
        ),
        key=lambda r: (r["km"], r["name"]),
    )  # 3
    if medical:
        unknown.append({"field": "응급의료기관 지정 등급", "reason": "자료 없음"})
    return {
        "home": home,
        "by_sigungu": rows,
        "situation_rooms": {"central": CENTRAL_ROOM, "sido": f"{sido} 재난안전상황실"},  # 2
        "emergency_medical": medical,
        "unknown": unknown,
    }
