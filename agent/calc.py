# ruff: noqa: E501  (계산 값 문장은 한 줄로 둔다)
"""판단 계산 값(11/17 네 단계): 판단 문단에 필요한 더하기 · 빼기를 판정 값으로 미리 해 둔다.

판단(상황과 상태를 맞대 본 결과)은 계산이 들어가는 일이 많다(「안평초등학교 수용 109명에 275명 → 166명 넘침」).
LLM에게 계산을 맡기면 틀리고, 검사(check.py)는 판정에 없는 새 숫자를 막는다. 그래서 계산은 여기서 하고,
문장은 LLM이 이 값으로 쓴다. 여기 값은 [판단 계산 값]으로 프롬프트에 들어가고 검사에서 허용 숫자가 된다.

    lines = for_item("E4", items_by_id)   # ["(가정) …", "지금 대피할 괴산리 · 신월리 275명 → …", …]

- E4: 대피소(머물 곳 후보) 수용 비교 — 첫 후보를 여러 마을이 함께 쓰면 합계, 마을마다 첫 후보 · 두 후보 합계
- E5: 마을 → 대피 장소 짝(첫 후보가 모자란 마을은 두 곳)
- (11/19 없앰) E3 「거주자 이송을 맡을 기관」: 담당 지정은 규칙층이 아니라 제안 문단이 쓴다(재설계 D3)
- E1: 구역별 마을 수(이미 불이 닿은 마을도 위험구역), S4: 투입 헬기 · 인력(S3 판정)
규칙 엔진 E4 facts(shelters)를 읽는다. 구조가 바뀌면 여기와 tests/unit/test_agent_calc.py를 함께 고친다.
"""

from typing import Any

from agent.situate import josa

ASSUME = "(가정) 주민이 모두 대피소에 머문다고 보고, 첫 후보 = 마을에서 가장 가까운 머물 곳 후보로 계산했습니다."


def _n(x: int) -> str:
    return f"{x:,}"


def _who(village: str, people: int) -> str:
    return f"{village} {_n(people)}명"


def _join(names: list[str]) -> str:
    return " · ".join(names)


def _hubs(row: dict[str, Any]) -> list[dict[str, Any]]:
    """머물 곳 후보(수용 인원이 있는 것만, 가까운 순서 그대로)."""
    return [h for h in row.get("hub_sites") or [] if h.get("name") and h.get("capacity")]


def _rows(e4: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in (e4.get("facts") or {}).get("shelters") or [] if r.get("people")]


def _now(row: dict[str, Any]) -> bool:
    return row.get("zone") == "burning" or row.get("until") == "지금"


def shelter_check(e4: dict[str, Any]) -> dict[str, Any]:
    """E4 판정 → 대피소 수용 비교(숫자). lines는 사람이 읽는 문장."""
    rows = _rows(e4)
    first: dict[str, dict[str, Any]] = {}
    for r in rows:
        hubs = _hubs(r)
        if hubs:
            g = first.setdefault(hubs[0]["name"], {"cap": hubs[0]["capacity"], "rows": []})
            g["rows"].append(r)
    over_shared = {
        name for name, g in first.items() if sum(r["people"] for r in g["rows"]) > g["cap"]
    }
    lines: list[str] = []
    short: list[str] = []  # 첫 후보로 장소를 정할 수 없는 마을
    enough: list[str] = []
    # 1. 지금 대피할 마을이 함께 쓰는 첫 후보
    for name, g in first.items():
        now = [r for r in g["rows"] if _now(r)]
        if now:
            tot = sum(r["people"] for r in now)
            who = _join([r["village"] for r in now])
            if tot > g["cap"]:
                lines.append(
                    f"지금 대피할 {who} {_n(tot)}명 → 첫 후보 {name}(수용 {_n(g['cap'])}명)에 {_n(tot - g['cap'])}명이 넘칩니다."
                )
            elif len(now) > 1:
                lines.append(
                    f"지금 대피할 {who} {_n(tot)}명 → 첫 후보 {name}(수용 {_n(g['cap'])}명)에 다 들어갑니다."
                )
    # 2. 여러 마을이 첫 후보로 함께 쓰는 대피소
    for name, g in first.items():
        if len(g["rows"]) >= 2:
            tot = sum(r["people"] for r in g["rows"])
            who = _join([r["village"] for r in g["rows"]])
            tail = f"{_n(tot - g['cap'])}명이 넘칩니다" if tot > g["cap"] else "다 들어갑니다"
            lines.append(
                f"{josa(f'{name}(수용 {_n(g["cap"])}명)', '을', '를')} 첫 후보로 둔 마을 {len(g['rows'])}곳({who})을 합치면 {_n(tot)}명이라 {tail}."
            )
    # 3. 마을마다
    for r in rows:
        v, p, hubs = r["village"], r["people"], _hubs(r)
        if not hubs:
            short.append(v)
            lines.append(f"{josa(_who(v, p), '은', '는')} 머물 곳 후보가 없습니다.")
            continue
        c1 = hubs[0]["capacity"]
        if p <= c1:
            if hubs[0]["name"] in over_shared:
                short.append(v)  # 혼자로는 들어가지만 함께 쓰면 넘친다(위 2.)
            else:
                enough.append(v)
            continue
        short.append(v)
        if len(hubs) >= 2:
            c12 = c1 + hubs[1]["capacity"]
            pair = f"{hubs[0]['name']}({_n(c1)}명) · {hubs[1]['name']}({_n(hubs[1]['capacity'])}명)"
            if p <= c12:
                lines.append(
                    f"{josa(_who(v, p), '은', '는')} 첫 후보만으로는 {_n(p - c1)}명이 모자라고, 두 후보 {josa(pair, '을', '를')} 합치면 {_n(c12)}명으로 들어갑니다."
                )
            else:
                lines.append(
                    f"{josa(_who(v, p), '은', '는')} 두 후보 {josa(pair, '을', '를')} 합쳐도 {_n(c12)}명이라 {_n(p - c12)}명이 모자랍니다."
                )
        else:
            lines.append(
                f"{josa(_who(v, p), '은', '는')} 후보가 {hubs[0]['name']}({_n(c1)}명) 하나라 {_n(p - c1)}명이 모자랍니다."
            )
    if enough:
        lines.append(f"첫 후보 하나로 충분한 마을: {_join(enough)}.")
    # 회차 1 3차: LLM이 「잠재 위험구역 마을」에 5시간 안 마을까지 넣음 → 구역을 함께 알린다
    p8 = [r["village"] for r in rows if r.get("zone") == "p8"]
    if p8:
        lines.append(
            f"잠재 위험구역(5 ~ 8시간) 마을은 {_join(p8)}뿐입니다. 나머지는 위험구역입니다."
        )
    pairs = {r["village"]: _hubs(r)[0]["name"] for r in rows if r["village"] in enough}
    return {"lines": ([ASSUME] + lines) if lines else [], "short": short, "pairs": pairs}


def shelter_table(e4: dict[str, Any]) -> list[dict[str, Any]]:
    """E4 「대피소 지정」 자세히 보기(11/24 BLUF): 첫 후보에 몰리는 인원 표 — shelter_check와 같은 계산을 표로.
    행 = ① 여러 마을이 함께 쓰거나 넘치는 첫 후보 ② 첫 후보만으로 모자라는 마을의 두 후보 합계."""
    rows: list[list[str]] = []
    first: dict[str, dict[str, Any]] = {}
    for r in _rows(e4):
        hubs = _hubs(r)
        if hubs:
            first.setdefault(hubs[0]["name"], {"cap": hubs[0]["capacity"], "rows": []})[
                "rows"
            ].append(r)
    for name, g in first.items():
        tot = sum(r["people"] for r in g["rows"])
        if len(g["rows"]) < 2 and tot <= g["cap"]:
            continue
        res = f"{_n(tot - g['cap'])}명 넘침" if tot > g["cap"] else "다 들어감"
        rows.append(
            [
                f"{name}({_n(g['cap'])}명)",
                _join([r["village"] for r in g["rows"]]),
                f"{_n(tot)}명",
                res,
            ]
        )
    for r in _rows(e4):
        hubs, p = _hubs(r), r["people"]
        if len(hubs) < 2 or p <= hubs[0]["capacity"]:
            continue
        c12 = hubs[0]["capacity"] + hubs[1]["capacity"]
        if p <= c12:
            continue  # 두 후보로 들어가는 마을은 표에 넣지 않는다(목업: 모자라는 마을만)
        res = f"{_n(p - c12)}명 모자람"
        rows.append(
            [
                f"{hubs[0]['name']} + {hubs[1]['name']}({_n(c12)}명)",
                f"{r['village']}(두 후보)",
                f"{_n(p)}명",
                res,
            ]
        )
    if not rows:
        return []
    return [
        {
            "type": "table",
            "caption": "첫 후보에 몰리는 인원",
            "head": ["대피소(수용)", "첫 후보로 둔 마을", "인원", "결과"],
            "rows": rows,
        }
    ]


def pairing_lines(e4: dict[str, Any]) -> list[str]:
    """E5: 마을 → 대피 장소 짝. 첫 후보가 넉넉하면 한 곳, 모자라면 두 곳(먼저 쓸 곳부터, 11/25).

    전에는 첫 후보가 모자란 마을을 「장소를 아직 정할 수 없음」으로 넘겨서 E4는 대피소를 정하고
    E5는 「정해지지 않았다」고 쓰는 모순이 생겼다(QA 4-8)."""
    enough = shelter_check(e4)["pairs"]
    out = []
    for r in _rows(e4):
        names = [h["name"] for h in _hubs(r)]
        if names:
            out.append(f"{r['village']} → {_join(names[: 1 if r['village'] in enough else 2])}")
    return (["마을과 대피 장소의 짝(E4 후보, 먼저 쓸 곳부터):"] + out) if out else []


def zone_lines(e1: dict[str, Any]) -> list[str]:
    """E1: 구역별 마을 수(회차 1 시험에서 LLM이 이미 불이 닿은 마을을 빼고 「위험구역 5곳」으로 셈 → 코드가 센다)."""
    z = (e1.get("facts") or {}).get("zones") or {}
    burning, p5, p8 = (
        [r["name"] for r in z.get(k) or [] if r.get("name")] for k in ("burning", "p5", "p8")
    )
    out = []
    if burning or p5:
        out.append(
            f"위험구역(5시간 안{', 이미 불이 닿은 마을 포함' if burning else ''}) {len(burning) + len(p5)}개 리: "
            + _join(burning + p5)
            + (f" (이미 닿음 {len(burning)}개 리: {_join(burning)})" if burning else "")
        )
    if p8:
        out.append(f"잠재 위험구역(5 ~ 8시간) {len(p8)}개 리: {_join(p8)}")
    return out


def deployed_lines(s3: dict[str, Any]) -> list[str]:
    """S4: 투입 헬기 · 인력(S3 판정) — S4 판정에는 투입 자원이 없어 상태 문단을 쓸 수 없었다."""
    d = (s3.get("facts") or {}).get("deployed") or {}
    heli, crew = (d.get("helicopter") or {}).get("total"), (d.get("crew") or {}).get("total")
    parts = []
    if heli:
        parts.append(f"헬기 {_n(heli)}대")
    if crew:
        parts.append(f"인력 {_n(crew)}명")
    return [f"지금 투입된 진화자원(S3 판정): {', '.join(parts)}"] if parts else []


def for_item(item_id: str, items_by_id: dict[str, dict[str, Any]]) -> list[str]:
    """그 항목의 [판단 계산 값] 문장(없으면 빈 목록)."""
    get = items_by_id.get
    try:
        if item_id == "E4" and get("E4"):
            return shelter_check(get("E4"))["lines"]
        if item_id == "E5" and get("E4"):
            return pairing_lines(get("E4"))
        if item_id == "E1" and get("E1"):
            return zone_lines(get("E1"))
        if item_id == "S4" and get("S3"):
            return deployed_lines(get("S3"))
    except (KeyError, TypeError, ValueError):
        return []
    return []
