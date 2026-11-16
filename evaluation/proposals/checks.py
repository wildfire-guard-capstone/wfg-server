"""제안 항목에서 버그 유형별 사례를 찾는다(2026-11-16 버그 목록 B01 ~ B07).

행(row) 하나 = 제안 항목 하나, `proposal_items`와 같은 모양:
    {"run", "proposal_id", "item_code", "state", "sections", "evidence", "detail"}
`detail`은 규칙 엔진 항목 입력(facts 포함), `sections`는 문단 · 목록 · 표 블록이다.

검사는 출력만 본다(고친 코드를 읽지 않는다). 그래서 고치기 전 · 후에 같은 입력으로 돌려
건수를 비교한다.
판정 기준은 매뉴얼 · 엔진 판정과 맞대기만 한다 — 산불 지식으로 옳고 그름을 가리지 않는다.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from typing import Any

Row = dict[str, Any]

STAGE_FLOOR_HA = {"1": 10.0, "2": 100.0}  # 매뉴얼 p.73: 1단계 10 ~ 100 ha 미만 · 2단계 100 ha 이상
WIND_RISING_MS = 1.5  # agent/situation.py flags()["wind_rising"]: 최대 풍속 ≥ 지금 풍속 + 1.5 m/s
DAYBREAK = ("일출", "날이 밝")  # 「일출 뒤 공중 진화」는 밤의 공중 진화 지시가 아니다
EXAMPLES = 3

THRESHOLD = re.compile(r"(?:확산대응\s*)?([12])단계\s*기준\s*(\d+(?:\.\d+)?)\s*ha")
TWO_TIMES = re.compile(r"피해면적이\s*(\d{1,2}):(\d{2})에[^,]*,\s*(\d{1,2}):(\d{2})에")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n")
STRONG_WIND_PREMISE = re.compile(r"강풍이 풀리|강풍 해제")
STAGE3 = re.compile(r"확산\s?대응\s*3단계")

BUGS = {
    "B01": "S1 단계 기준 면적 · 시각이 틀림",
    "B02": "있을 수 없는 단계(2단계에서 격상 · 3단계)",
    "B03": "S4 바람이 세지지 않는데 「강풍이 풀리면」",
    "B04": "밤에 공중 진화 · 헬기 투입 지시(S3 · S4)",
    "B05": "인용이 근거 원문에 없음(quote_ok = False)",
    "B06": "항목 표시 실패(state = failed)",
    "B07": "E4 마을 인구보다 수용 인원이 적은 대피소를 먼저 권함",
}


def paragraphs(row: Row) -> list[dict[str, Any]]:
    return [
        b
        for s in row.get("sections") or []
        for b in s.get("blocks") or []
        if b.get("type") == "paragraph"
    ]


def facts(row: Row) -> dict[str, Any]:
    return (row.get("detail") or {}).get("facts") or {}


def _clip(text: str, word: str = "", width: int = 70) -> str:
    """word가 든 문장(없으면 첫 문장)을 width자까지 — 예시에 붙인다."""
    sentences = [s.strip() for s in SENTENCE.split(text) if s.strip()] or [text.strip()]
    hit = next((s for s in sentences if word and word in s), sentences[0])
    return hit if len(hit) <= width else hit[: width - 1] + "…"


def _norm(text: str | None) -> str:
    return re.sub(r"[\s.]", "", text or "")


def b01(row: Row) -> list[str]:
    """S1 「n단계 기준 X ha」의 X가 매뉴얼 하한과 다르다.
    「피해면적이 A에 …, B에 …」의 시각이 거꾸로다."""
    if row.get("item_code") != "S1":
        return []
    out = []
    for p in paragraphs(row):
        body = p.get("body") or ""
        for stage, ha in THRESHOLD.findall(body):
            if float(ha) != STAGE_FLOOR_HA[stage]:
                out.append(f"{stage}단계 기준 {ha} ha(매뉴얼 p.73은 {STAGE_FLOOR_HA[stage]:g} ha)")
        m = TWO_TIMES.search(body)
        if m:
            first, second = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
            if 0 < first - second < 12 * 60:  # 자정을 넘긴 경우는 거꾸로가 아니다
                out.append(f"시각 순서가 거꾸로: {m[1]}:{m[2]} → {m[3]}:{m[4]}")
    return out


def b02(row: Row) -> list[str]:
    """대응단계는 초기 대응 · 1단계 · 2단계뿐(매뉴얼 p.13 · p.73). S1에서 「3단계」 · 2단계에서의
    「격상」, 다른 항목에서 「확산대응 3단계」(주민대피 · 위기경보 단계와 헷갈리지 않게)."""
    s1 = row.get("item_code") == "S1"
    now = (facts(row).get("now") or {}).get("stage")
    out = []
    for p in paragraphs(row):
        body = p.get("body") or ""
        if STAGE3.search(body) or (s1 and "3단계" in body):
            out.append(_clip(body, "3단계"))
        elif s1 and ("2단계에서 격상" in body or (now == "2단계" and "격상" in body)):
            out.append(_clip(body, "격상"))
    return out


def b03(row: Row) -> list[str]:
    """바람이 세지지 않는데(agent/situation.py의 wind_rising이 아님) 강풍이 풀리는 때를 전제한다."""
    if row.get("item_code") != "S4":
        return []
    f = facts(row)
    top = (f.get("max") or {}).get("ms")
    now = (f.get("now") or {}).get("wind_ms")
    if top is not None and now is not None and top >= now + WIND_RISING_MS:
        return []
    return [
        _clip(p.get("body") or "", "강풍")
        for p in paragraphs(row)
        if STRONG_WIND_PREMISE.search(p.get("body") or "")
    ]


def b04(rows: list[Row]) -> list[tuple[Row, str]]:
    """지금 밤(S4 facts sun.now_is_night)인데 S3 · S4에서 공중 진화 · 헬기 투입을 지시한다.
    제안서 하나(같은 run · proposal_id)의 항목을 함께 본다. 일출 뒤 · 강풍 문장(B03)은 뺀다."""
    night = any(
        (facts(r).get("sun") or {}).get("now_is_night") for r in rows if r.get("item_code") == "S4"
    )
    if not night:
        return []
    out = []
    for row in rows:
        if row.get("item_code") not in ("S3", "S4"):
            continue
        for p in paragraphs(row):
            for s in SENTENCE.split(p.get("body") or ""):
                aerial = "공중" in s or ("헬기" in s and "투입" in s)
                if (
                    aerial
                    and "십시오" in s
                    and "강풍" not in s
                    and not any(w in s for w in DAYBREAK)
                ):
                    out.append((row, _clip(s, "공중" if "공중" in s else "헬기")))
    return out


def b05(row: Row) -> list[str]:
    """인용이 근거 원문에 없다. 인용이 제안 문장의 일부면(지침 문장을 인용으로 붙임) 따로 적는다."""
    out = []
    for p in paragraphs(row):
        if p.get("quote_ok") is not False:
            continue
        quote = p.get("quote") or ""
        own = bool(_norm(quote)) and _norm(quote) in _norm(p.get("body"))
        out.append(f"{'지시문을 인용으로' if own else '원문과 다른 인용'}: {quote[:40]}")
    return out


def b06(row: Row) -> list[str]:
    """검사를 통과한 제안이 없어 항목이 비었다."""
    return ["검사를 통과한 제안 없음"] if row.get("state") == "failed" else []


def b07(row: Row) -> list[str]:
    """E4 문단이 마을과 함께 처음 이름을 댄 대피소의 수용 인원이 그 마을 인구보다 적다."""
    if row.get("item_code") != "E4":
        return []
    bodies = [p.get("body") or "" for p in paragraphs(row)]
    out = []
    for sh in facts(row).get("shelters") or []:
        village, people = sh.get("village"), sh.get("people")
        sites = [
            s
            for s in (sh.get("village_sites") or []) + (sh.get("hub_sites") or [])
            if s.get("name")
        ]
        if not village or not people:
            continue
        for body in bodies:
            if village not in body:
                continue
            named = sorted(
                (body.find(s["name"]), i) for i, s in enumerate(sites) if s["name"] in body
            )
            if not named:
                continue
            site = sites[named[0][1]]
            if site.get("capacity") and site["capacity"] < people:
                out.append(f"{village} {people}명 → {site['name']}(수용 {site['capacity']}명)")
            break
    return out


ITEM_CHECKS: dict[str, Callable[[Row], list[str]]] = {
    "B01": b01,
    "B02": b02,
    "B03": b03,
    "B05": b05,
    "B06": b06,
    "B07": b07,
}


def _finding(bug: str, row: Row, detail: str) -> dict[str, Any]:
    return {
        "bug": bug,
        "run": row.get("run", 0),
        "proposal_id": row.get("proposal_id"),
        "item_code": row.get("item_code"),
        "detail": detail,
    }


def find(rows: Iterable[Row]) -> list[dict[str, Any]]:
    """모든 검사를 돌려 사례 목록을 돌려준다(버그 · run · 제안서 · 항목 순)."""
    rows = list(rows)
    out = [
        _finding(bug, row, d)
        for row in rows
        for bug, check in ITEM_CHECKS.items()
        for d in check(row)
    ]
    groups: dict[tuple[Any, Any], list[Row]] = defaultdict(list)
    for row in rows:
        groups[(row.get("run", 0), row.get("proposal_id"))].append(row)
    out += [_finding("B04", row, d) for group in groups.values() for row, d in b04(group)]
    return sorted(
        out, key=lambda f: (f["bug"], f["run"], f["proposal_id"] or 0, f["item_code"] or "")
    )


def summarize(rows: Iterable[Row], findings: list[dict[str, Any]]) -> dict[str, Any]:
    """건수 요약. 출력 = 제안서 하나를 한 번 쓴 것(run · 제안서). 「걸린 출력 · 항목」은 사례가
    여럿이어도 한 번만 센다."""
    rows = list(rows)
    paras = [p for r in rows for p in paragraphs(r)]
    quotes = Counter(p.get("quote_ok") for p in paras)
    checked = quotes[True] + quotes[False]
    bugs = {}
    for bug, title in BUGS.items():
        fs = [f for f in findings if f["bug"] == bug]
        bugs[bug] = {
            "title": title,
            "outputs": len({(f["run"], f["proposal_id"]) for f in fs}),
            "items": len({(f["run"], f["proposal_id"], f["item_code"]) for f in fs}),
            "findings": len(fs),
            "by_item": dict(Counter(f["item_code"] for f in fs)),
            "examples": [
                f"run {f['run']} · 제안서 {f['proposal_id']} · {f['item_code']}: {f['detail']}"
                for f in fs[:EXAMPLES]
            ],
        }
    return {
        "items": len(rows),
        "proposal_ids": sorted({r["proposal_id"] for r in rows}),
        "runs": len({r.get("run", 0) for r in rows}),
        "outputs": len({(r.get("run", 0), r.get("proposal_id")) for r in rows}),
        "paragraphs": len(paras),
        "states": dict(Counter(r.get("state") for r in rows)),
        "quote_match_rate": round(quotes[True] / checked, 3) if checked else None,
        "bugs": bugs,
    }
