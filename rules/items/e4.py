"""E4 대피소 지정 및 관련 통제: 엔진 JSON 묶음에서 E4 입력을 만든다.

판정하지 않는다. `shelters`(마을별 후보)와 `roads`(큰 도로 구간)를 옮긴다. 어느 대피소로
안내할지 · 누구에게 통제를 요청할지는 LLM이 p.75 · 79 · 95로 쓴다. 대피 인원(전 주민 / 고령자)은
판정하지 않고 인구 · 65세 이상을 둘 다 넘긴다. 마을도 도로도 없으면 E4를 만들지 않는다.
"""

from typing import Any

ID, NAME = "E4", "대피소 지정 및 관련 통제"


def _village(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "village": r["village"],
        "emd": r["emd"],
        "zone": r["zone"],
        "from": r["from"],
        "until": r["until"],
        "night": r["night"],
        "people": r["population"],
        "elderly": r["pop65_est"],
        "village_sites": r["village_sites"],
        "hub_sites": r["hub_sites"],
        "excluded": r["excluded"],
        "no_options": r["no_options"],
    }


def _road(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "road": r["road"],
        "section": r["section"],
        "grade": r["grade"],
        "zone": r["zone"],
        "from": r["from"],
        "until": r["until"],
        "km": r["km"],
        "police": r["police"],
        "operator": r["manager"],
    }


def e4_input(bundles: dict[str, Any]) -> dict[str, Any] | None:
    """E4 입력을 만든다. 마을 · 도로가 모두 없으면 None.

    1. 마을별 대피소 후보 · 8시간 안 대피소 수
    2. 큰 도로 구간
    3. 상황 = 있는 것의 이름: has_options / no_options(후보 없는 마을이 있음) · roads_at_risk
    """
    sh, rd = bundles["shelters"], bundles["roads"]
    villages = [_village(r) for r in sh["villages"]]  # 1
    roads = [_road(r) for r in rd["sections"]]  # 2
    if not villages and not roads:
        return None
    status = []  # 3
    if villages:
        status.append("no_options" if sh["counts"]["no_options"] else "has_options")
    if roads:
        status.append("roads_at_risk")
    return {
        "id": ID,
        "name": NAME,
        "status": status,
        "finding": _finding(sh, roads),
        "facts": {"shelters": villages, "excluded_in_p8": sh["counts"]["in_p8"], "roads": roads},
        "targets": [],
        "withheld": sh["unknown"] + rd["unknown"],
        "rule_basis": sh["basis"] + rd["basis"],
    }


def _finding(sh: dict[str, Any], roads: list[dict[str, Any]]) -> str:
    """「대피 대상 8개 리 모두 대피소 후보 있음 · 8시간 범위 안 대피소 0곳 / 큰 도로 구간 …」."""
    c = sh["counts"]
    parts = []
    if c["villages"]:
        head = (
            f"대피 대상 {c['villages']}개 리 중 {c['no_options']}개 리 대피소 후보 없음"
            if c["no_options"]
            else f"대피 대상 {c['villages']}개 리 모두 대피소 후보 있음"
        )
        parts.append(f"{head} · 8시간 범위 안 대피소 {c['in_p8']}곳")
    if roads:
        listed = " · ".join(f"{r['road']} {r['section']}({r['until']})" for r in roads)
        parts.append(f"5시간 안 큰 도로 구간: {listed}")
    return " / ".join(parts)
