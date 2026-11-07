"""E6 유관기관 요청: 요청 표(대피 쪽). LLM을 부르지 않는다(action 블록 없음, 11/7).

판정하지 않는다. 표 행은 요청 표 틀(items/requests.py)이 엔진 묶음에서 만든다. 옛 E7(인명 수색 ·
구조 요청)은 11/7에 빠졌다. 요청할 일이 없으면 E6을 만들지 않는다.
"""

from typing import Any

from rules.items.requests import e_rows

ID, NAME = "E6", "유관기관 요청"


def e6_input(bundles: dict[str, Any]) -> dict[str, Any] | None:
    """E6 입력: 요청 행 수 · 표 행. 행이 없으면 None."""
    rows = e_rows(bundles)
    if not rows:
        return None
    return {
        "id": ID,
        "name": NAME,
        "status": "coordination_needed",
        "finding": f"다른 기관에 요청할 일 {len(rows)}건",
        "facts": {"request_count": len(rows)},
        "rows": rows,
        "targets": [],
        "withheld": [],
        "rule_basis": [],
    }
