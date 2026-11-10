"""`resources` 묶음: 투입된 헬기 · 인력 · 장비(상황 보고), 기지별 투입, 인력을 보낸 기관.

투입 합계 = 상황 보고 입력(산림청 양식 칸)이다. 기지별 투입 = 자원 DB 연결(`deployed_incident_id`)을
기지마다 센 값(헬기 · 차량만, 11/7) — 추가로 부를 곳을 고를 때 이미 나간 기지를 알 수 있게 한다.
둘은 출처가 달라 더하지 않는다(자원 DB에 없는 군 · 경찰 헬기 등은 기지별에 없음).
보고 전이면 투입 수는 모름(null + 이유) — 0과 나눈다.
"""

from typing import Any

from rules.context import Context
from rules.core.places import Deployed

LABELS = {
    "helicopter": ("산림청", "지자체", "소방", "군", "경찰", "미상"),
    "crew": ("진화대", "공무원", "소방", "군", "경찰", "기타"),
    "vehicle": ("지휘·진화차", "소방차", "기타"),
}
UNKNOWN_FIXED = [
    {"field": "인력 보유 · 대기 수", "reason": "자료 없음"},
    {"field": "인력 위치", "reason": "위치 정보 없음"},
]


def _counts(values: dict[str, int] | None, labels: tuple[str, ...]) -> dict[str, Any]:
    """라벨 고정 묶음. 보고 전(None)이면 합계 · 칸 모두 null. 모르는 라벨이 들어오면 오류."""
    if values is None:
        return {"total": None, "by": [{"label": lb, "n": None} for lb in labels]}
    extra = set(values) - set(labels)
    if extra:
        raise ValueError(f"상황 보고에 모르는 라벨: {sorted(extra)}")
    by = [{"label": lb, "n": values.get(lb, 0)} for lb in labels]
    return {"total": sum(x["n"] for x in by), "by": by}


def _orgs(
    ctx: Context, crew: dict[str, int], home: str, unknown: list[dict[str, str]]
) -> list[str]:
    """인력을 보낸 기관: 진화대 · 공무원 → 산불 시군의 산림 부서, 소방 → 관할 소방서(관서).

    군 · 경찰 · 기타는 소속 기관 자료가 없어 넣지 않는다.
    """
    orgs = []
    if crew.get("진화대") or crew.get("공무원"):
        dept = ctx.inputs.forest_dept.get(home)
        if dept is None:
            unknown.append({"field": f"{home} 산림 부서 이름", "reason": "자료 없음"})
        else:
            orgs.append(dept)
    if crew.get("소방"):
        stations = [f for f in ctx.inputs.fire_stations if f.kind == "소방서" and f.sigungu == home]
        if stations:
            orgs.append(stations[0].name)
        else:
            unknown.append({"field": f"{home} 관할 소방서", "reason": "자료 없음"})
    return orgs


def _by_base(ctx: Context, unknown: list[dict[str, str]]) -> list[dict[str, Any]]:
    """기지별 투입(자원 연결): 이 산불에 연결된 헬기 · 차량이 있는 기지, 발화지에서 가까운 순."""
    bases = ctx.inputs.bases
    if any(b.deployed_helicopters is None and b.deployed_vehicles is None for b in bases):
        unknown.append({"field": "기지별 투입 일부", "reason": "자원 상태 자료 없음"})
    rows = sorted(
        (b for b in bases if b.deployed_helicopters or b.deployed_vehicles),
        key=lambda b: (ctx.inputs.ignition.distance(b.geom), b.name),
    )
    return [
        {
            "name": b.name,
            "kind": b.kind,
            "sigungu": b.sigungu,
            "helicopters": b.deployed_helicopters,
            "vehicles": b.deployed_vehicles,
        }
        for b in rows
    ]


def resources_bundle(ctx: Context) -> dict[str, Any]:
    """`resources`: 투입 수(라벨 고정) · 기지별 투입 · 인력을 보낸 기관.

    1. 상황 보고가 없으면 투입 수 = 모름
    2. 헬기 · 인력 · 장비마다 라벨 고정, 없는 라벨은 0, 합계
    3. 인력을 보낸 기관과 그 수(문장의 「두 책임자」)
    4. 기지별 투입 = 자원 연결이 있는 기지(상황 보고와 따로)
    """
    d: Deployed | None = ctx.inputs.deployed
    home = ctx.span_now.sigungu[0]
    unknown = [dict(u) for u in UNKNOWN_FIXED]
    if d is None:  # 1
        unknown.insert(0, {"field": "투입 수", "reason": "상황 보고 전"})
    crew = _counts(None if d is None else dict(d.crew), LABELS["crew"])  # 2
    orgs = [] if d is None else _orgs(ctx, dict(d.crew), home, unknown)  # 3
    return {
        "source": "상황 보고 입력",
        "reported_at": None if d is None else d.reported_at,
        "helicopter": _counts(None if d is None else dict(d.helicopter), LABELS["helicopter"]),
        "crew": {**crew, "orgs": orgs, "org_count": len(orgs)},
        "vehicle": _counts(None if d is None else dict(d.vehicle), LABELS["vehicle"]),
        "by_base": {
            "source": "자원 연결(자원 DB, 헬기 · 차량만)",
            "bases": _by_base(ctx, unknown),
        },  # 4
        "unknown": unknown,
    }
