"""E1 위험구역 설정: 엔진 JSON 묶음에서 E1 입력을 만든다.

판정하지 않는다. `villages`의 구간별 마을(J1)을 고르고 틀을 채운다. 「리 단위로 구역을
설정하라」는 LLM이 p.75로 쓴다. P8 안에 마을이 없으면 E1을 만들지 않는다.
"""

from typing import Any

ID, NAME = "E1", "위험구역 설정"
ZONES = ("burning", "p5", "p8")


def _names(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"name": r["name"], "emd": r["emd"]} for r in rows]


def by_emd(rows: list[dict[str, Any]], mark_burning: bool = False) -> str:
    """마을을 읍면별로 묶어 쓴다(같은 값끼리 묶기): 「안평면 괴산리·신월리, 의성읍 철파리」.

    `mark_burning`이면 이미 닿은 마을 뒤에 「(이미 닿음)」을 붙인다.
    """
    groups: dict[str, list[str]] = {}
    for r in rows:
        mark = "(이미 닿음)" if mark_burning and r["first_p"] == 0 else ""
        groups.setdefault(r["emd"], []).append(r["name"] + mark)
    return ", ".join(f"{emd} {'·'.join(names)}" for emd, names in groups.items())


def e1_input(bundles: dict[str, Any]) -> dict[str, Any] | None:
    """E1 입력을 만든다. 마을이 없으면 None.

    1. 구간별 마을의 이름 · 읍면을 고른다
    2. 상황: 위험구역(이미 닿음 포함) 마을이 있으면 risk_zone, 잠재만 있으면 potential_only
    3. finding 틀을 채운다
    """
    v = bundles["villages"]
    if not any(v[z] for z in ZONES):
        return None
    facts = {"zones": {z: _names(v[z]) for z in ZONES}}  # 1
    within = v["burning"] + v["p5"]
    status = "risk_zone" if within else "potential_only"  # 2
    risk = (
        f"위험구역 {v['totals']['within_p5']['count']}곳: {by_emd(within, mark_burning=True)}"
        if within
        else "위험구역 없음"
    )
    potential = (
        f" / 잠재 위험구역 {v['totals']['p8']['count']}곳: {by_emd(v['p8'])}" if v["p8"] else ""
    )
    return {
        "id": ID,
        "name": NAME,
        "status": status,
        "finding": risk + potential,  # 3
        "facts": facts,
        "targets": [f"village:{r['id']}" for z in ZONES for r in v[z]],
        "withheld": v["unknown"],
        "rule_basis": ["SM-p075"],
    }
