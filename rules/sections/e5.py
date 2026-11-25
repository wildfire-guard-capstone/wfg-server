"""E5 섹션: E5 입력을 보고 LLM 입력의 `sections`를 만든다.

판정하지 않는다. a1 「재난문자 · 자막방송」(세부는 E6) + Go · Set 목록, Go 읍면이 있으면 a2
「함께 쓸 전파 수단」(Go 문자에는 대피 장소가 들어가므로 E4 대피소와 이어짐).
"""

from typing import Any

from rules.items.e5 import by_stage

ANCHOR_IDS = {"p.76": "SM-p076", "p.78": "SM-p078"}


def e5_sections(e5: dict[str, Any]) -> dict[str, Any]:
    """E5 입력으로 섹션을 만든다.

    1. 「1. 재난문자·방송」: a1 재난문자 · 자막방송 — 늘 넣음 + 단계별 목록(있는 단계만)
    2. 「2. 전파 수단」: a2 함께 쓸 전파 수단 — Go 읍면이 있을 때
    """
    areas = e5["facts"]["areas"]
    rows = [
        {"stage": stage, "text": f"**{label} 단계**: {listed}"}
        for stage, label in (("go", "Go"), ("set", "Set"))
        if (listed := by_stage(areas, stage))
    ]
    blocks: list[dict[str, Any]] = [
        {
            "type": "action",
            "id": "E5-a1",
            "topic": "재난문자 · 자막방송",
            "use": ["areas", "jurisdiction"],
            "anchors": ["p.76", "p.78"],
            "see": "E6",
        },
        {"type": "list", "style": "bullet", "writer": "조립기", "rows": rows},
    ]  # 1
    sections = [{"title": "1. 재난문자·방송", "blocks": blocks}]
    if any(a["stage"] == "go" for a in areas):  # 2
        action = {
            "type": "action",
            "id": "E5-a2",
            "topic": "함께 쓸 전파 수단",
            "use": ["areas"],
            "anchors": ["p.76"],
        }
        sections.append({"title": "2. 전파 수단", "blocks": [action]})
    anchors = [
        a for s in sections for b in s["blocks"] if b["type"] == "action" for a in b["anchors"]
    ]
    return {
        "sections": sections,
        "do_not_state": [w["field"] for w in e5["withheld"]],
        "anchor_basis": list(dict.fromkeys(ANCHOR_IDS[a] for a in anchors)),
    }
