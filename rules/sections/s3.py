"""S3 섹션: S3 입력을 보고 LLM 입력의 `sections`를 만든다.

판정하지 않는다. 「1. 투입 현황」 a1 · 「2. 동원 범위」 a2(세부는 S6) · 범위가 넓어지면 a3 + ※.
"""

from typing import Any

ANCHOR_IDS = {
    "p.77": ["SM-p077"],
    "p.78": ["SM-p078"],
    "고시 별표": ["NOTI-MOBL-t0-p1", "NOTI-MOBL-t0-p2"],
}
NOTE = "※ 추가 투입 대수와 배치는 산정 기준이 없어 표시하지 않았습니다."


def _action(n: int, topic: str, use: list[str], anchors: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "type": "action",
        "id": f"S3-a{n}",
        "topic": topic,
        "use": use,
        "anchors": anchors,
        **extra,
    }


def s3_sections(s3: dict[str, Any]) -> dict[str, Any]:
    """S3 입력으로 섹션을 만든다.

    1. a1 투입 자원 — 늘 넣음
    2. a2 현재 단계 동원 범위(세부는 S6) — 늘 넣음
    3. a3 5시간 뒤 동원 범위 — 범위가 넓어질 때(scope_expands)
    """
    second = [
        _action(2, "현재 단계 동원 범위", ["scope", "available_now"], ["고시 별표"], see="S6")
    ]
    if s3["status"] == "scope_expands":
        second.append(_action(3, "5시간 뒤 동원 범위", ["scope", "added_at_p5"], ["고시 별표"]))
    second.append({"type": "note", "writer": "조립기", "text": NOTE})
    sections = [
        {
            "title": "1. 투입 현황",
            "blocks": [_action(1, "투입 자원", ["deployed"], ["p.77", "p.78"])],
        },
        {"title": "2. 동원 범위", "blocks": second},
    ]
    anchors = [
        a for s in sections for b in s["blocks"] if b["type"] == "action" for a in b["anchors"]
    ]
    return {
        "sections": sections,
        "do_not_state": [w["field"] for w in s3["withheld"]],
        "anchor_basis": list(dict.fromkeys(i for a in anchors for i in ANCHOR_IDS[a])),
    }
