"""S4 섹션: S4 입력을 보고 LLM 입력의 `sections`를 만든다.

「1. 시간대 · 바람」은 늘, 「2. 헬기 재투입」은 바람이 세질 때만
(8시간 안 최대 풍속 ≥ 지금 + 1.5 m/s). 바람이 세지지 않는데 「강풍이 풀리면 헬기를 …」을
쓰게 하지 않는다(11/16 버그 B03). 그때 ※는 1에 붙인다.
"""

from typing import Any

ANCHOR_IDS = {"p.77": "SM-p077", "p.78": "SM-p078"}
NOTE = "※ 헬기 운항에 관한 정보가 없기 때문에 운항 가능 여부는 판단하지 않았습니다."
WIND_RISE_MS = 1.5  # agent/situation.py WIND_RISE_MS와 같은 값


def _action(n: int, topic: str, use: list[str], anchors: list[str]) -> dict[str, Any]:
    return {"type": "action", "id": f"S4-a{n}", "topic": topic, "use": use, "anchors": anchors}


def wind_rises(s4: dict[str, Any]) -> bool:
    """agent/situation.py의 wind_rising과 같은 기준."""
    f = s4["facts"]
    return (f["max"]["ms"] or 0) >= (f["now"]["wind_ms"] or 0) + WIND_RISE_MS


def s4_sections(s4: dict[str, Any]) -> dict[str, Any]:
    """1. 「1. 시간대 · 바람」: a1 시간대 · a2 바람
    2. 「2. 헬기 재투입」: a3 + ※ — 바람이 세질 때만. 아니면 ※는 1에."""
    note = {"type": "note", "writer": "조립기", "text": NOTE}
    first = [
        _action(1, "시간대", ["sun"], ["p.77", "p.78"]),
        _action(2, "바람", ["now", "wind", "max"], ["p.78"]),
    ]
    if wind_rises(s4):
        sections = [
            {"title": "1. 시간대 · 바람", "blocks": first},
            {
                "title": "2. 헬기 재투입",
                "blocks": [_action(3, "헬기 재투입", ["wind"], ["p.78"]), note],
            },
        ]
    else:
        sections = [{"title": "1. 시간대 · 바람", "blocks": [*first, note]}]
    return {
        "sections": sections,
        "do_not_state": [w["field"] for w in s4["withheld"]],
        "anchor_basis": list(ANCHOR_IDS.values()),
    }
