"""E1 섹션: E1 입력을 보고 LLM 입력의 `sections`를 만든다.

판정하지 않는다. a1 「리 단위 구역」 하나와 조립기 목록(구간별 마을).
"""

from typing import Any

from rules.items.e1 import by_emd

LABELS = {
    "burning": "위험구역(이미 닿음)",
    "p5": "위험구역(5시간 안)",
    "p8": "잠재 위험구역(5~8시간)",
}


def e1_sections(e1: dict[str, Any], bundles: dict[str, Any]) -> dict[str, Any]:
    """E1 입력과 엔진 묶음으로 섹션을 만든다.

    1. a1 리 단위 구역 — 늘 넣음
    2. 목록: 마을이 있는 구간마다 한 줄, 읍면별로 묶음
    3. 언급 금지 · 문단 근거 ID
    """
    v = bundles["villages"]
    blocks: list[dict[str, Any]] = [
        {
            "type": "action",
            "id": "E1-a1",
            "topic": "리 단위 구역",
            "use": ["zones"],
            "anchors": ["p.75"],
        },
    ]  # 1
    rows = [
        {"zone": z, "text": f"**{LABELS[z]}**: {by_emd(v[z]).replace('·', ', ')}"}
        for z in ("burning", "p5", "p8")
        if v[z]
    ]  # 2
    blocks.append({"type": "list", "style": "bullet", "writer": "조립기", "rows": rows})
    return {
        "sections": [{"title": "", "blocks": blocks}],
        "do_not_state": [w["field"] for w in e1["withheld"]],  # 3
        "anchor_basis": ["SM-p075"],
    }
