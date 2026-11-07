"""E4 섹션: E4 입력을 보고 LLM 입력의 `sections`를 만든다.

판정하지 않는다. 마을 시설 = 「먼저 모일 곳」, 거점 시설 = 「머물 곳」은 이름 바꾸기다.
블록은 그 마을 · 도로가 있을 때만 넣는다.
"""

from typing import Any

ANCHOR_IDS = {"p.75": "SM-p075", "p.79": "SM-p079", "p.95": "SM-p095"}


def _when(r: dict[str, Any]) -> str:
    if r["zone"] == "burning":
        return "지금(이미 닿음)"
    return f"{r['from']} ~ {r['until']} 닿을 수 있음" + ("(밤)" if r["night"] else "")


def _sites(sites: list[dict[str, Any]]) -> str:
    parts = []
    for s in sites:
        cap = "수용 인원 모름" if s["capacity"] is None else f"수용 {s['capacity']:,}명"
        mark = ", 예외: 8시간 안" if s["safety"] == "exception" else ""
        parts.append(f"{s['name']}({s['km']} km, {cap}{mark})")
    return " 또는 ".join(parts)


def _row(r: dict[str, Any]) -> dict[str, Any]:
    """「**괴산리**(지금(이미 닿음), 147명): 먼저 모일 곳 … · 머물 곳 …」."""
    people = f"{r['people']:,}명"
    if r["zone"] == "p8" and r["elderly"] is not None:
        people += f" · 65세 이상 {r['elderly']:,}명"
    head = f"**{r['village']}**({_when(r)}, {people})"
    if r["no_options"]:
        return {"village": r["village"], "text": f"{head}: 쓸 수 있는 대피소 후보 없음"}
    parts = []
    if r["village_sites"]:
        parts.append(f"먼저 모일 곳 {_sites(r['village_sites'])}")
    if r["hub_sites"]:
        parts.append(f"머물 곳 {_sites(r['hub_sites'])}")
    return {"village": r["village"], "text": f"{head}: {' · '.join(parts)}"}


def _action(n: int, topic: str, use: list[str], anchors: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "type": "action",
        "id": f"E4-a{n}",
        "topic": topic,
        "use": use,
        "anchors": anchors,
        **extra,
    }


def e4_sections(e4: dict[str, Any]) -> dict[str, Any]:
    """E4 입력으로 섹션을 만든다.

    1. 「1. 대피소 지정」: 위험구역 마을이 있으면 a1 + 목록, 잠재 위험구역 마을이 있으면 a2 + 목록,
       ※ 8시간 안 대피소 수 · 개방 여부 확인
    2. 「2. 관련 통제」: 도로 구간이 있으면 a3(세부는 E6)
    """
    f = e4["facts"]
    risk = [r for r in f["shelters"] if r["zone"] != "p8"]
    potential = [r for r in f["shelters"] if r["zone"] == "p8"]
    sections = []
    if f["shelters"]:  # 1
        blocks: list[dict[str, Any]] = []
        if risk:
            blocks.append(_action(1, "위험구역 마을 대피소", ["shelters"], ["p.79"]))
            blocks.append(
                {
                    "type": "list",
                    "style": "bullet",
                    "writer": "조립기",
                    "rows": [_row(r) for r in risk],
                }
            )
        if potential:
            blocks.append(_action(2, "잠재 위험구역 마을 대피소", ["shelters"], ["p.75"]))
            blocks.append(
                {
                    "type": "list",
                    "style": "bullet",
                    "writer": "조립기",
                    "rows": [_row(r) for r in potential],
                }
            )
        n = f["excluded_in_p8"]
        head = (
            f"※ 8시간 범위 안에 든 대피소 {n}곳은 후보에서 뺐습니다"
            "(안전한 후보가 없을 때만 예외 후보)."
            if n
            else "※ 8시간 범위 안에 든 대피소가 없어 제외한 곳은 없습니다."
        )
        text = f"{head} 대피소의 실제 개방 여부와 남은 수용 인원은 확인이 필요합니다."
        blocks.append({"type": "note", "writer": "조립기", "text": text})
        sections.append({"title": "1. 대피소 지정", "blocks": blocks})
    if f["roads"]:  # 2
        sections.append(
            {
                "title": "2. 관련 통제",
                "blocks": [
                    _action(3, "큰 도로", ["roads"], ["p.79", "p.95"], see="E6"),
                ],
            }
        )
    anchors = [
        a for s in sections for b in s["blocks"] if b["type"] == "action" for a in b["anchors"]
    ]
    return {
        "sections": sections,
        "do_not_state": [w["field"] for w in e4["withheld"]] + ["대피 경로"],
        "anchor_basis": list(dict.fromkeys(ANCHOR_IDS[a] for a in anchors)),
    }
