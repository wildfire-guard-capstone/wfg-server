"""S2 섹션: S2 입력을 보고 LLM 입력의 `sections`를 만든다.

판정하지 않는다. 「1. 진화 우선순위」(a1 + 번호 목록 + ※) · 「2. 보호」(주택 · 송전시설 · 문화유산
블록은 그 대상이 있을 때만). 번호 목록의 순위 이름은 매뉴얼 p.77 고정 표다.
"""

from typing import Any

ANCHOR_IDS = {"p.77": "SM-p077", "p.79": "SM-p079", "p.95": "SM-p095"}
NOTE_FIELDS = ("군사시설", "중요 산림자원")


def _when(r: dict[str, Any]) -> str:
    return "지금(이미 닿음)" if r["zone"] == "burning" else f"{r['from']} ~ {r['by']}"


def _life_row(life: dict[str, Any]) -> str:
    """① 인명: 시각 구간별 → 읍면별로 마을, 그 마을 취약시설은 「(○○ 주변)」(같은 값끼리 묶기)."""
    by_village: dict[str, list[str]] = {}
    for f in life["vulnerable"]:
        by_village.setdefault(f["village"], []).append(f["name"])
    groups: dict[str, dict[str, list[str]]] = {}
    for r in life["villages"]:
        name = r["name"] + (" (대피 완료)" if r["evacuated"] else "")
        if r["name"] in by_village:
            name += f"({'·'.join(by_village[r['name']])} 주변)"
        groups.setdefault(_when(r), {}).setdefault(r["emd"], []).append(name)
    body = ", ".join(
        f"{when} " + ", ".join(f"{emd} {'·'.join(names)}" for emd, names in emds.items())
        for when, emds in groups.items()
    )
    return f"① **인명**: {body}"


def _infra_row(f: dict[str, Any]) -> str | None:
    items = [
        f"{p['name']}({p['km_p5']} km)" if p["km_p5"] else f"{p['name']}(8시간 안 {p['km_p8']} km)"
        for p in f["power"]
    ]
    items += [f"{s['name']}({s['emd']} {s['village']})" for s in f["substations"] + f["temple"]]
    items += [f"{s['name']}({s['emd']} {s['village']})" for s in f["heritage"]["items"]]
    return f"② **송전시설 · 문화유산**: {', '.join(items)}" if items else None


def _action(n: int, label: str | None, topic: str, use: list[str], anchors: list[str]) -> dict:
    block = {"type": "action", "id": f"S2-a{n}", "topic": topic, "use": use, "anchors": anchors}
    if label:
        block.update(label=label, see="S6")
    return block


def s2_sections(s2: dict[str, Any]) -> dict[str, Any]:
    """S2 입력으로 섹션을 만든다.

    1. 「1. 진화 우선순위」: a1(늘) + 있는 순위만 번호 목록 + 위치 정보가 없는 대상 ※
    2. 「2. 보호」: 주택이 있으면 a2 · 송전선 · 변전소가 있으면 a3 · 사찰 · 국가유산이 있으면 a4
    """
    f = s2["facts"]
    rows = []
    if f["life"]["villages"]:
        rows.append({"rank": 1, "kind": "인명", "text": _life_row(f["life"])})
    if (infra := _infra_row(f)) is not None:
        rows.append({"rank": 2, "kind": "송전시설·문화유산", "text": infra})
    if f["houses"]["in_p5"]:
        houses = ", ".join(f"{r['village']} 최소 {r['houses']}동" for r in f["houses"]["in_p5"])
        rows.append({"rank": 3, "kind": "주택", "text": f"③ **주택**: {houses}"})
    missing = [w["field"] for w in s2["withheld"] if w["field"] in NOTE_FIELDS]
    first = [
        _action(
            1,
            None,
            "진화 우선순위",
            ["life", "power", "substations", "temple", "heritage", "houses"],
            ["p.77"],
        ),
        {"type": "list", "style": "numbered", "writer": "조립기", "rows": rows},
    ]
    if missing:
        text = f"※ {'과 '.join(missing)}은 위치 정보가 없어 반영하지 않았습니다."
        first.append({"type": "note", "writer": "조립기", "text": text})
    second = []  # 2
    if f["houses"]["in_p5_total"]:
        second.append(_action(2, "주택", "주택 보호 배치", ["houses"], ["p.79"]))
    if f["power"] or f["substations"]:
        second.append(_action(3, "송전시설", "송전시설 보호", ["power", "substations"], ["p.95"]))
    if f["temple"] or f["heritage"]["items"]:
        second.append(
            _action(4, "문화유산", "문화유산 이동 · 경비", ["temple", "heritage"], ["p.79"])
        )
    sections = [{"title": "1. 진화 우선순위", "blocks": first}]
    if second:
        sections.append({"title": "2. 보호", "blocks": second})
    anchors = [
        a for s in sections for b in s["blocks"] if b["type"] == "action" for a in b["anchors"]
    ]
    return {
        "sections": sections,
        "do_not_state": [w["field"] for w in s2["withheld"]],
        "anchor_basis": list(dict.fromkeys(ANCHOR_IDS[a] for a in anchors)),
    }
