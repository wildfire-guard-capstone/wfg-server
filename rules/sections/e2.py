"""E2 섹션: E2 입력을 보고 LLM 입력의 `sections`를 만든다.

판정하지 않는다. 블록 주제는 고정, 블록은 그 마을들이 있을 때만 넣는다. 조립기 목록은 사실만
쓴다 — 「13:50까지 대피」 같은 시한이 아니라 「12:50 ~ 13:50 닿을 수 있음」(구간 끝만 쓰면
시한이 최대 1시간 늦어짐). 시한은 LLM이 p.75로 정한다.
"""

from typing import Any

ANCHOR_IDS = {"p.75": "SM-p075", "p.76": "SM-p076", "p.79": "SM-p079"}


def _when(r: dict[str, Any]) -> str:
    """시각 구간 이름. 그 마을이 밤에 닿으면 뒤에 「(밤)」(같은 구간 마을은 밤 여부도 같음)."""
    if r["zone"] == "burning":
        return "지금(이미 닿음)"
    night = "(밤)" if r["night"] else ""
    return f"{r['from']} ~ {r['until']} 닿을 수 있음{night}"


def _rows(villages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 시각 구간끼리 묶어 한 줄씩(같은 값끼리 묶기). 현장 입력은 표시만 붙인다.

    - 대피 완료 → 「(대피 완료)」 · 시각이 이미 지남 → 「(예상 시각 경과 — 새 화선 보고 필요)」
    - 문자가 나갔는데 대피 완료가 아님 → 「○○리는 12:40 대피 문자가 나갔지만 대피 완료 보고가
      없습니다」(11/7 넣는 규칙)
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in villages:
        groups.setdefault(_when(r), []).append(r)
    rows = []
    for when, rs in groups.items():
        names = []
        notes = []
        for r in rs:
            mark = " (대피 완료)" if r["evacuated"] else ""
            mark += " (예상 시각 경과 — 새 화선 보고 필요)" if r["passed"] else ""
            names.append(f"**{r['name']}**({r['people']:,}명){mark}")
            if r["alert_sent_at"] and not r["evacuated"]:
                sent = f"{r['name']}는 {r['alert_sent_at']} 대피 문자가 나갔지만"
                notes.append(f"{sent} 대피 완료 보고가 없습니다.")
        text = f"{when}: {', '.join(names)}" + (". " + " ".join(notes) if notes else "")
        rows.append({"when": when, "text": text})
    return rows


def _action(n: int, topic: str, use: list[str], anchors: list[str]) -> dict[str, Any]:
    return {"type": "action", "id": f"E2-a{n}", "topic": topic, "use": use, "anchors": anchors}


def e2_sections(e2: dict[str, Any]) -> dict[str, Any]:
    """E2 입력으로 섹션을 만든다.

    1. 위험구역 마을이 있으면 a1 「위험구역 마을 대피」 + 시각 구간별 목록
    2. 잠재 위험구역 마을이 있으면 a2 「잠재 위험구역 사전대피」 + 목록
    3. 언급 금지 · 문단 근거 ID
    """
    f = e2["facts"]
    blocks: list[dict[str, Any]] = []
    if f["immediate"]:  # 1
        blocks.append(
            _action(
                1, "위험구역 마을 대피", ["immediate", "immediate_total"], ["p.75", "p.76", "p.79"]
            )
        )
        blocks.append(
            {"type": "list", "style": "bullet", "writer": "조립기", "rows": _rows(f["immediate"])}
        )
    if f["pre_evac"]:  # 2
        blocks.append(
            _action(2, "잠재 위험구역 사전대피", ["pre_evac", "sunset", "now_is_night"], ["p.75"])
        )
        blocks.append(
            {"type": "list", "style": "bullet", "writer": "조립기", "rows": _rows(f["pre_evac"])}
        )
    anchors = [a for b in blocks if b["type"] == "action" for a in b["anchors"]]
    return {
        "sections": [{"title": "", "blocks": blocks}],
        "do_not_state": [w["field"] for w in e2["withheld"]],  # 3
        "anchor_basis": list(dict.fromkeys(ANCHOR_IDS[a] for a in anchors)),
    }
