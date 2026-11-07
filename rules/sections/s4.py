"""S4 섹션: S4 입력을 보고 LLM 입력의 `sections`를 만든다(블록 주제 고정, 늘 같은 블록)."""

from typing import Any

ANCHOR_IDS = {"p.77": "SM-p077", "p.78": "SM-p078"}
NOTE = "※ 헬기 운항에 관한 정보가 없기 때문에 운항 가능 여부는 판단하지 않았습니다."


def _action(n: int, topic: str, use: list[str], anchors: list[str]) -> dict[str, Any]:
    return {"type": "action", "id": f"S4-a{n}", "topic": topic, "use": use, "anchors": anchors}


def s4_sections(s4: dict[str, Any]) -> dict[str, Any]:
    """1. 「1. 시간대 · 바람」: a1 시간대 · a2 바람  2. 「2. 헬기 재투입」: a3 + ※."""
    sections = [
        {"title": "1. 시간대 · 바람", "blocks": [
            _action(1, "시간대", ["sun"], ["p.77", "p.78"]),
            _action(2, "바람", ["now", "wind", "max"], ["p.78"]),
        ]},
        {"title": "2. 헬기 재투입", "blocks": [
            _action(3, "헬기 재투입", ["wind"], ["p.78"]),
            {"type": "note", "writer": "조립기", "text": NOTE},
        ]},
    ]  # fmt: skip
    return {
        "sections": sections,
        "do_not_state": [w["field"] for w in s4["withheld"]],
        "anchor_basis": list(ANCHOR_IDS.values()),
    }
