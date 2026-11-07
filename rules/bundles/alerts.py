"""`alerts` 묶음: 읍면별 Set · Go 단계(J9)와 직전 제안서 대비 변화, 문자 발송 기록.

문안 · 송출 요청 대상은 정하지 않는다(문안은 사람이, 송출 요청은 LLM이 p.76 · 78로 쓴다).
직전 비교는 직전 제안서의 엔진 JSON(`proposals.context`의 이 묶음)과 한다 — 엔진은 항목을 모른다.
"""

from typing import Any

from rules.context import Context

ORDER = {"set": 0, "go": 1}


def _change(prev: str | None, now: str) -> str:
    """직전과 비교: 처음 들어옴 new · Set → Go up · 같음 same · Go → Set down."""
    if prev is None:
        return "new"
    if ORDER[now] > ORDER[prev]:
        return "up"
    return "down" if ORDER[now] < ORDER[prev] else "same"


def alerts_bundle(ctx: Context) -> dict[str, Any]:
    """`alerts`: P8 안 마을이 있는 읍면마다 단계.

    1. 마을을 (시군구, 읍면)으로 묶는다. 읍면 순서 = 그 읍면에서 가장 먼저 닿는 마을 순
    2. J9: 읍면 안 마을 중 이미 닿음 · 위험구역(P0 ~ P5)이 하나라도 있으면 go,
       잠재 위험구역(P6 ~ P8)만 있으면 set
    3. 직전 제안서 단계와 비교 → new · up · same · down
    4. 직전에는 있었는데 이번에는 P8 밖인 읍면 → `removed`
    5. 문자 발송 기록 = 현장 입력에 대피 문자 발송 시각이 있는 마을
    """
    areas: dict[tuple[str, str], dict[str, Any]] = {}
    for r in ctx.villages:  # 1 (ctx.villages는 닿는 순)
        v = r.village
        empty = {"sigungu": v.sigungu, "emd": v.emd, "villages": [], "go": False}
        a = areas.setdefault((v.sigungu, v.emd), empty)
        a["villages"].append(v.name)
        a["go"] |= r.p <= 5  # 2
    prev = {(sg, emd): level for sg, emd, level in ctx.inputs.prev_alerts}
    rows = []
    for key, a in areas.items():
        level = "go" if a["go"] else "set"
        rows.append(
            {
                "sigungu": a["sigungu"],
                "emd": a["emd"],
                "level": level,
                "villages": a["villages"],
                "change": _change(prev.get(key), level),  # 3
                "prev": prev.get(key),
            }
        )
    removed = [
        {"sigungu": sg, "emd": emd, "prev": level}
        for (sg, emd), level in prev.items()
        if (sg, emd) not in areas
    ]  # 4
    names = {v.id: v for v in ctx.inputs.villages}
    log = [
        {"village": names[vid].name, "emd": names[vid].emd, "at": f.alert_at, "kind": "대피 문자"}
        for vid, f in ctx.inputs.field.items()
        if f.alert_at and vid in names
    ]  # 5
    log.sort(key=lambda x: (x["at"], x["village"]))
    return {
        "areas": rows,
        "removed": removed,
        "log": log,
        "basis": ["SM-p076"],
        "unknown": [],
    }
