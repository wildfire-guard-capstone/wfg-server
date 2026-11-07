"""S5 진화인력 안전 · 투입 관리: 엔진 JSON 묶음에서 S5 입력을 만든다.

판정하지 않는다. `resources.crew`(투입 인력 · 보낸 기관) · `spread`(방향 · 다음에 닿는 마을) ·
`weather.max`(최대 풍속)를 옮긴다. 장비 확인 · 인솔자 지정 · 철수 조건 · 통신 확인은 LLM이
규정(제8 · 9조, 별표 2 · 3)으로 쓴다. 「풍향 급변」은 판정하지 않는다(기준 없음).
보고된 투입 인력이 0이면 S5를 만들지 않는다(보고 전이면 만든다 — 안전 확인이 빠지지 않게).
"""

from typing import Any

ID, NAME = "S5", "진화인력 안전·투입 관리"
RULE_BASIS = ["SM-p077", "REG-ROLE-a8", "REG-ROLE-a9-h01-03", "REG-ROLE-a9-h04"]
RULE_BASIS += [f"REG-ROLE-t{t}-p{p}" for t in (2, 3) for p in range(1, 5)]


def s5_input(bundles: dict[str, Any]) -> dict[str, Any] | None:
    """S5 입력을 만든다. 보고된 투입 인력이 0이면 None.

    1. 투입 인력 · 소속별 · 보낸 기관과 그 수
    2. 화선 전방 = 주 확산 방향 + 다음에 닿는 마을
    3. 바람 = 최대 풍속 · 풍향 · 시각(처음 ~ 끝)
    """
    crew, sp, w = bundles["resources"]["crew"], bundles["spread"], bundles["weather"]
    if crew["total"] == 0:
        return None
    hours = w["max"]["hours"]
    label = hours[0] if len(hours) == 1 else f"{hours[0]}~{hours[-1]}"
    facts = {
        "crew_total": crew["total"],  # 1
        "crew_by": crew["by"],
        "leaders": crew["orgs"],
        "org_count": crew["org_count"],
        "front": {  # 2
            "dir": sp["dir"],
            "km": sp["km"],
            "villages": [r["name"] for r in sp["next_villages"]],
        },
        "wind": {"label": label, "dir": w["max"]["dir"], "ms": w["max"]["ms"]},  # 3
    }
    crew_text = "상황 보고 전" if crew["total"] is None else f"{crew['total']}명"
    front = facts["front"]
    ahead = f"({'·'.join(front['villages'])})" if front["villages"] else ""
    finding = (
        f"투입 인력 {crew_text} · 주 확산 방향 {front['dir'] or '모름'}{ahead}"
        f" · 최대 풍속 {w['max']['ms']} m/s({w['max']['dir']}, {label})"
    )
    withheld = [u for u in bundles["resources"]["unknown"] if u["field"] != "인력 보유 · 대기 수"]
    withheld += [u for u in w["unknown"] if u["field"] == "풍향 급변"] + sp["unknown"]
    return {
        "id": ID,
        "name": NAME,
        "status": "crew_deployed",
        "finding": finding,
        "facts": facts,
        "targets": [],
        "withheld": withheld,
        "rule_basis": list(RULE_BASIS),
    }
