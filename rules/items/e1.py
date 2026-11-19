"""E1 위험구역 설정: 엔진 JSON 묶음에서 E1 입력을 만든다.

판정하지 않는다. `villages`의 구간별 마을(J1)을 고르고 틀을 채운다. 「리 단위로 구역을
설정하라」는 LLM이 p.75로 쓴다. P8 안에 마을이 없으면 E1을 만들지 않는다.
마을 구간은 리 경계가 처음 닿는 P이고(11/20 D1 — 경계 걸침도 대피 대상),
닿는 행정리를 괄호로 붙인다.
「경계만 걸침」 칸(11/19 R3)은 남겨 두지만 이제 늘 비어 있다.
"""

from typing import Any

from rules.items.names import admin_names, label_of

ID, NAME = "E1", "위험구역 설정"
ZONES = ("burning", "p5", "p8")
EDGE_LABEL = "경계만 걸침(8시간 안 주택 없음)"


def village_targets(v: dict[str, Any], zones: tuple[str, ...] = ZONES) -> list[str]:
    """지도 대상: 법정리 + 닿는 행정리."""
    rows = [r for z in zones for r in v[z]]
    ids = [f"village:{r['id']}" for r in rows]
    return ids + [f"admin_village:{a['id']}" for r in rows for a in r.get("admin_villages") or []]


def _names(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"name": r["name"], "emd": r["emd"], "admin_villages": admin_names(r)} for r in rows]


def _edges(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"name": r["name"], "emd": r["emd"], "houses": r["houses"]} for r in rows]


def by_emd(rows: list[dict[str, Any]], mark_burning: bool = False, sep: str = "·") -> str:
    """마을을 읍면별로 묶어 쓴다(같은 값끼리 묶기): 「안평면 괴산리·신월리, 의성읍 철파리」.

    `mark_burning`이면 이미 닿은 마을 뒤에 「(이미 닿음)」을 붙인다.
    """
    groups: dict[str, list[str]] = {}
    for r in rows:
        mark = "(이미 닿음)" if mark_burning and r["first_p"] == 0 else ""
        groups.setdefault(r["emd"], []).append(label_of(r) + mark)
    return ", ".join(f"{emd} {sep.join(names)}" for emd, names in groups.items())


def e1_input(bundles: dict[str, Any]) -> dict[str, Any] | None:
    """E1 입력을 만든다. 마을이 없으면 None.

    1. 구간별 마을의 이름 · 읍면 · 닿는 행정리, 경계만 걸친 마을을 고른다
    2. 상황: 위험구역(이미 닿음 포함) 마을이 있으면 risk_zone, 잠재만 있으면 potential_only
    3. finding 틀을 채운다
    """
    v = bundles["villages"]
    if not any(v[z] for z in ZONES):
        return None
    facts = {"zones": {z: _names(v[z]) for z in ZONES}, "edge_only": _edges(v["edge"])}  # 1
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
    edge = f" / {EDGE_LABEL} {len(v['edge'])}곳: {by_emd(v['edge'])}" if v["edge"] else ""
    return {
        "id": ID,
        "name": NAME,
        "status": status,
        "finding": risk + potential + edge,  # 3
        "facts": facts,
        "targets": village_targets(v),
        "withheld": [],  # 구역만 다루므로 인원 모름은 E2가 알림
        "rule_basis": ["SM-p075"],
    }
