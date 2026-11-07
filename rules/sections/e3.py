"""E3 섹션: E3 입력을 보고 LLM 입력의 `sections`를 만든다.

판정하지 않는다. 블록 주제는 고정, 블록은 그 시설이 있을 때만 넣는다. 조립기 목록은 사실만
쓴다(「16:50 전」 같은 시한이 아니라 「15:50 ~ 16:50 닿을 수 있음」). 시한은 LLM이 정한다.
"""

from typing import Any

ANCHOR_IDS = {"p.75": "SM-p075", "p.79": "SM-p079", "p.84": "SM-p084", "p.95": "SM-p095"}


def _when(r: dict[str, Any]) -> str:
    if r["zone"] == "burning":
        return "지금(이미 닿음)"
    night = "(밤)" if r["night"] else ""
    return f"{r['from']} ~ {r['until']} 닿을 수 있음{night}"


def _rows(facilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """마을마다 한 줄(같은 값끼리 묶기): 「15:50 ~ 16:50 닿을 수 있음, **원당리**: 해안1병원 …」."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in facilities:
        groups.setdefault((_when(r), r["village"]), []).append(r)
    rows = []
    for (when, village), rs in groups.items():
        names = ", ".join(f"{r['name']}({r['kind'] or '종류 모름'})" for r in rs)
        rows.append({"village": village, "text": f"{when}, **{village}**: {names}"})
    return rows


def _action(n: int, topic: str, use: list[str], anchors: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "type": "action",
        "id": f"E3-a{n}",
        "topic": topic,
        "use": use,
        "anchors": anchors,
        **extra,
    }


def e3_sections(e3: dict[str, Any]) -> dict[str, Any]:
    """E3 입력으로 섹션을 만든다.

    1. 위험구역 시설이 있으면 「1. 우선 대피」: a1 「위험구역 시설」 + 마을별 목록
       낮에만 운영하는 시설이 있으면 a2 「낮에만 운영하는 시설」(근거 없는 확인 문단)
    2. 잠재 위험구역 시설이 있으면 「2. 사전대피」: a3 「잠재 위험구역 시설」 + 목록
    3. 「3. 이송」: a4 「거주자 이송」(세부는 E6) — 시설이 있으면 늘 넣음
    """
    f = e3["facts"]
    sections = []
    if f["immediate"]:  # 1
        blocks = [
            _action(1, "위험구역 시설", ["immediate"], ["p.79", "p.84"]),
            {"type": "list", "style": "bullet", "writer": "조립기", "rows": _rows(f["immediate"])},
        ]
        if f["daytime_only_count"]:
            blocks.append(
                _action(
                    2,
                    "낮에만 운영하는 시설",
                    ["daytime_only_count", "weekday", "now_is_night"],
                    [],
                    kind="check",
                )
            )
        sections.append({"title": "1. 우선 대피", "blocks": blocks})
    if f["pre_evac"]:  # 2
        sections.append(
            {
                "title": "2. 사전대피",
                "blocks": [
                    _action(3, "잠재 위험구역 시설", ["pre_evac"], ["p.75"]),
                    {
                        "type": "list",
                        "style": "bullet",
                        "writer": "조립기",
                        "rows": _rows(f["pre_evac"]),
                    },
                ],
            }
        )
    sections.append(  # 3
        {
            "title": "3. 이송",
            "blocks": [_action(4, "거주자 이송", ["ministries"], ["p.95"], see="E6")],
        }
    )
    anchors = [
        a for s in sections for b in s["blocks"] if b["type"] == "action" for a in b["anchors"]
    ]
    return {
        "sections": sections,
        "do_not_state": [w["field"] for w in e3["withheld"]],
        "anchor_basis": list(dict.fromkeys(ANCHOR_IDS[a] for a in anchors)),
    }
