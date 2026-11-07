"""E3 취약시설 조치: 엔진 JSON 묶음에서 E3 입력을 만든다.

판정하지 않는다. `vulnerable`에서 구간(이름표)으로 거르고 `time`의 요일 · 지금 밤을 옮긴다.
「우선 대피 · 이송」 · 「사전대피 · 이송 준비」와 시한은 LLM이 p.75 · 79 · 84 · 95로 정한다.
P8 안 마을에 취약시설이 없으면 E3를 만들지 않는다.
"""

from typing import Any

ID, NAME = "E3", "취약시설 조치"


def _row(r: dict[str, Any]) -> dict[str, Any]:
    """시설 한 줄: 그대로 옮긴다."""
    return {
        "name": r["name"],
        "kind": r["kind"],
        "village": r["village"],
        "emd": r["emd"],
        "zone": r["zone"],
        "from": r["from"],
        "until": r["until"],
        "night": r["night"],
        "passed": r["passed"],
        "daytime_only": r["daytime_only"],
    }


def e3_input(bundles: dict[str, Any]) -> dict[str, Any] | None:
    """E3 입력을 만든다. 시설이 없으면 None.

    1. 5시간 안(이미 닿음 · 위험구역) 시설 / 잠재 위험구역 시설로 나눈다(이름표로 거르기)
    2. 낮에만 운영 개수 · 요일 · 지금 밤을 옮긴다
    3. finding 틀 — 사실만
    """
    v, t = bundles["vulnerable"], bundles["time"]
    rows = v["in_range"]
    if not rows:
        return None
    immediate = [_row(r) for r in rows if r["zone"] != "p8"]  # 1
    pre_evac = [_row(r) for r in rows if r["zone"] == "p8"]
    facts = {
        "immediate": immediate,
        "pre_evac": pre_evac,
        "daytime_only_count": v["counts"]["daytime_only"],  # 2
        "weekday": t["weekday"],
        "now_is_night": t["sun"]["now_is_night"],
    }
    return {
        "id": ID,
        "name": NAME,
        "status": "facilities_at_risk",
        "finding": _finding(immediate, pre_evac, v["counts"]),  # 3
        "facts": facts,
        "targets": [f"facility:{r['id']}" for r in rows],
        "withheld": v["unknown"],
        "rule_basis": v["basis"],
    }


def _list(rows: list[dict[str, Any]]) -> str:
    return " · ".join(f"{r['name']}({r['kind'] or '종류 모름'}, {r['village']})" for r in rows)


def _finding(
    immediate: list[dict[str, Any]], pre_evac: list[dict[str, Any]], counts: dict[str, int]
) -> str:
    """「위험구역 시설 7곳: 의성군공립요양병원(요양병원, 철파리) · … / 잠재 위험구역 시설 …」."""
    parts = []
    if immediate:
        parts.append(f"위험구역 시설 {counts['within_p5']}곳: {_list(immediate)}")
    if pre_evac:
        parts.append(f"잠재 위험구역 시설 {counts['p8']}곳: {_list(pre_evac)}")
    return " / ".join(parts)
