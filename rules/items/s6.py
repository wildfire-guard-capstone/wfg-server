"""S6 유관기관 동원 · 협조 요청: 요청 표(진화 쪽)를 만들고 대책회의 문단 자리를 둔다.

판정하지 않는다. 표 행은 요청 표 틀(items/requests.py)이 엔진 묶음에서 만든다. 대책회의 개최 ·
기관별 임무 부여는 LLM이 p.22 · 73으로 쓴다. 요청할 일이 없으면 S6을 만들지 않는다.
"""

from typing import Any

from rules.items.requests import s_rows

ID, NAME = "S6", "유관기관 동원·협조 요청"


def s6_input(bundles: dict[str, Any]) -> dict[str, Any] | None:
    """S6 입력: 요청 행 수 · 표 행. 행이 없으면 None."""
    rows = s_rows(bundles)
    if not rows:
        return None
    return {
        "id": ID,
        "name": NAME,
        "status": "coordination_needed",
        "finding": f"다른 기관에 요청할 일 {len(rows)}건 · 단계 발령 협의(산림청장)는 S1",
        "facts": {"request_count": len(rows)},
        "rows": rows,
        "targets": [],
        "withheld": bundles["agencies"]["unknown"],
        "rule_basis": ["SM-p022", "SM-p073"],
    }
