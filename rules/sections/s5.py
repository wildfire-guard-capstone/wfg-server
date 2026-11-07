"""S5 섹션: S5 입력을 보고 LLM 입력의 `sections`를 만든다(블록 주제 고정, 늘 같은 블록)."""

from typing import Any

ANCHOR_IDS = {
    "p.77": ["SM-p077"],
    "규정 제8조": ["REG-ROLE-a8"],
    "규정 제9조": ["REG-ROLE-a9-h01-03", "REG-ROLE-a9-h04"],
    "규정 별표 2": [f"REG-ROLE-t2-p{p}" for p in range(1, 5)],
    "규정 별표 3": [f"REG-ROLE-t3-p{p}" for p in range(1, 5)],
}


def _action(n: int, topic: str, use: list[str], anchors: list[str]) -> dict[str, Any]:
    return {"type": "action", "id": f"S5-a{n}", "topic": topic, "use": use, "anchors": anchors}


def s5_sections(s5: dict[str, Any]) -> dict[str, Any]:
    """1. 「1. 진화기관 책임자 확인」: a1 · a2  2. 「2. 현장 인솔자 전달」: a3 · a4 · a5."""
    sections = [
        {"title": "1. 진화기관 책임자 확인", "blocks": [
            _action(1, "위치추적장치 · 안전장구", ["leaders", "crew_total"],
                    ["p.77", "규정 제8조"]),
            _action(2, "현장 인솔자 · 안전 교육", ["leaders", "org_count"],
                    ["규정 제8조", "규정 제9조"]),
        ]},
        {"title": "2. 현장 인솔자 전달", "blocks": [
            _action(3, "위험 요소 전달", ["wind", "front"], ["규정 별표 2", "규정 별표 3"]),
            _action(4, "철수 조건", [], ["규정 별표 3"]),
            _action(5, "통신 상태", [], ["규정 별표 3"]),
        ]},
    ]  # fmt: skip
    anchors = [a for s in sections for b in s["blocks"] for a in b["anchors"]]
    return {
        "sections": sections,
        "do_not_state": [w["field"] for w in s5["withheld"]],
        "anchor_basis": list(dict.fromkeys(i for a in anchors for i in ANCHOR_IDS[a])),
    }
