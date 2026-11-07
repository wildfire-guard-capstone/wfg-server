"""S2 진화 우선지역 · 보호대상 · 시설 보호: 엔진 JSON 묶음에서 S2 입력을 만든다(11/7 확정 모양).

판정하지 않는다. `villages` · `vulnerable`(5시간 안) · `houses` · `protected`(8시간 안)에서 고른다.
진화 우선순위 묶음(① 인명 ② 국가기간시설 · 문화유산 ③ 가옥 등 재산)은 매뉴얼 p.77의 고정 표로
붙이는 이름이다(이름 바꾸기). 보호 조치와 요청 기관은 LLM이 p.79 · p.95로 쓴다.
"""

from typing import Any

ID, NAME = "S2", "진화 우선지역·보호대상·시설 보호"
COUNT_BASIS = "주용도 확인 건물 기준, 최소값"
UNKNOWN_FIXED = [{"field": "필요한 소방차 수와 배치 위치", "reason": "산정 기준 없음"}]


def _life_village(r: dict[str, Any]) -> dict[str, Any]:
    f = r["field"]
    return {
        "name": r["name"],
        "emd": r["emd"],
        "zone": r["zone"],
        "from": r["from"],
        "by": r["until"],
        "evac_order": f["evac_order"],
        "evacuated": f["evacuated"],
        "people": r["population"],
        "alert_sent_at": f["alert_at"],
    }


def _life_facility(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": r["name"],
        "kind": r["kind"],
        "village": r["village"],
        "zone": r["zone"],
        "by": r["until"],
        "daytime_only": r["daytime_only"],
    }


def _place(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": r["name"],
        "kind": r["kind"],
        "emd": r["emd"],
        "village": r["village"],
        "zone": r["zone"],
        "from": r["from"],
        "arrival": r["until"],
    }


def _power(r: dict[str, Any], p8_until: str) -> dict[str, Any]:
    return {
        "name": r["name"],
        "voltage_kv": r["voltage_kv"],
        "zone": r["zone"],
        "from": r["from"],
        "arrival": r["until"],
        "km_p5": r["km_p5"],
        "km_p8": r["km_p8"],
        "p8_until": p8_until,
        "p8_adds": r["p8_adds"],
    }


def s2_input(bundles: dict[str, Any]) -> dict[str, Any]:
    """S2 입력을 만든다. 5시간 범위는 늘 있으므로 늘 만든다.

    1. ① 인명 = 이미 닿음 + 5시간 안 마을 · 그 마을 취약시설
    2. ③ 주택 = 5시간 안 주택(리별)
    3. ② 송전선 · 변전소 · 전통사찰 · 국가유산 = 8시간 안(11/7)
    4. 상황 = 있는 순위의 이름(p.77 고정 표). 아무것도 없으면 forest_only
    """
    v, vul, h, pr = (
        bundles["villages"],
        bundles["vulnerable"],
        bundles["houses"],
        bundles["protected"],
    )
    p8_until = bundles["time"]["until"][8]
    facts = {
        "life": {  # 1
            "villages": [_life_village(r) for r in v["burning"] + v["p5"]],
            "vulnerable": [_life_facility(r) for r in vul["in_range"] if r["zone"] != "p8"],
        },
        "houses": {  # 2
            "in_p5": [
                {"village": r["village"], "houses": r["houses"]}
                for r in h["within_p5"]["by_village"]
            ],
            "in_p5_total": h["within_p5"]["total"],
            "count_basis": COUNT_BASIS if h["is_minimum"] else "",
        },
        "power": [_power(r, p8_until) for r in pr["power_lines"]],  # 3
        "substations": [_place(r) for r in pr["substations"]],
        "temple": [_place(r) for r in pr["temples"]],
        "heritage": {
            "total": pr["heritage"]["total"],
            "in_p8": pr["heritage"]["in_p8"],
            "items": [_place(r) for r in pr["heritage"]["items"]],
        },
    }
    status = []  # 4
    if facts["life"]["villages"]:
        status.append("life")
    if facts["power"] or facts["substations"] or facts["temple"] or facts["heritage"]["items"]:
        status.append("infrastructure")
    if facts["houses"]["in_p5_total"]:
        status.append("property")
    return {
        "id": ID,
        "name": NAME,
        "status": status or ["forest_only"],
        "finding": _finding(facts, v, vul),
        "facts": facts,
        "targets": _targets(v, vul, pr),
        "withheld": pr["unknown"] + [dict(u) for u in UNKNOWN_FIXED],
        "rule_basis": pr["basis"],
    }


def _targets(v: dict[str, Any], vul: dict[str, Any], pr: dict[str, Any]) -> list[str]:
    ids = [f"village:{r['id']}" for r in v["burning"] + v["p5"]]
    ids += [f"facility:{r['id']}" for r in vul["in_range"] if r["zone"] != "p8"]
    ids += [f"map_feature:power_line:{r['id']}" for r in pr["power_lines"]]
    ids += [
        f"facility:{r['id']}" for r in pr["substations"] + pr["temples"] + pr["heritage"]["items"]
    ]
    return ids


def _finding(facts: dict[str, Any], v: dict[str, Any], vul: dict[str, Any]) -> str:
    """「① 인명: 마을 7곳 · 취약시설 7곳 ② 154 kV 송전선 … 2.0 km · 운람사(전통사찰) ③ 주택 …」."""
    parts = []
    if facts["life"]["villages"]:
        n_v, n_f = v["totals"]["within_p5"]["count"], vul["counts"]["within_p5"]
        parts.append(f"① 인명: 마을 {n_v}곳" + (f" · 취약시설 {n_f}곳" if n_f else ""))
    infra = [f"{p['name']} {p['km_p5'] or p['km_p8']} km" for p in facts["power"]]
    infra += [f"{s['name']}({s['kind']})" for s in facts["substations"] + facts["temple"]]
    infra += [f"{s['name']}({s['kind']})" for s in facts["heritage"]["items"]]
    if infra:
        parts.append("② " + " · ".join(infra))
    if facts["houses"]["in_p5"]:
        houses = ", ".join(f"{r['village']} 최소 {r['houses']}동" for r in facts["houses"]["in_p5"])
        parts.append(f"③ 주택 {houses}")
    return " ".join(parts) if parts else "5시간 안 보호대상 없음"
