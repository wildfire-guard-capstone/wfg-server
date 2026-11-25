"""E1 섹션: E1 입력을 보고 LLM 입력의 `sections`를 만든다.

판정하지 않는다. 「1. 위험구역」(이미 닿음 · 5시간 안) · 「2. 잠재 위험구역」(5 ~ 8시간)마다
action 하나와 조립기 목록(구간별 마을). 마을이 없는 쪽은 만들지 않는다(11/25).
"""

from typing import Any

from rules.items.e1 import EDGE_LABEL, by_emd

LABELS = {
    "burning": "위험구역(이미 닿음)",
    "p5": "위험구역(5시간 안)",
    "p8": "잠재 위험구역(5~8시간)",
}


# (섹션 제목, action 주제, 구간)
GROUPS = (
    ("1. 위험구역", "위험구역 설정", ("burning", "p5")),
    ("2. 잠재 위험구역", "잠재 위험구역 설정", ("p8",)),
)


def e1_sections(e1: dict[str, Any], bundles: dict[str, Any]) -> dict[str, Any]:
    """E1 입력과 엔진 묶음으로 섹션을 만든다.

    1. 마을이 있는 쪽마다 섹션 하나: action + 구간별 목록(읍면별로 묶음)
    2. 경계만 걸친 마을은 마지막 섹션의 마지막 줄
    3. 언급 금지 · 문단 근거 ID
    """
    v = bundles["villages"]
    sections: list[dict[str, Any]] = []
    for n, (title, topic, zones) in enumerate(GROUPS, 1):  # 1
        rows = [
            {"zone": z, "text": f"**{LABELS[z]}**: {by_emd(v[z], sep=', ')}"} for z in zones if v[z]
        ]
        if not rows:
            continue
        sections.append(
            {
                "title": title,
                "blocks": [
                    {
                        "type": "action",
                        "id": f"E1-a{n}",
                        "topic": topic,
                        "use": ["zones", "edge_only"],
                        "anchors": ["p.75"],
                    },
                    {"type": "list", "style": "bullet", "writer": "조립기", "rows": rows},
                ],
            }
        )
    if v["edge"] and sections:  # 2
        row = {"zone": "edge", "text": f"**{EDGE_LABEL}**: {by_emd(v['edge'], sep=', ')}"}
        sections[-1]["blocks"][-1]["rows"].append(row)
    return {
        "sections": sections,
        "do_not_state": [w["field"] for w in e1["withheld"]],  # 3
        "anchor_basis": ["SM-p075"],
    }
