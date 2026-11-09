"""「변경」 판단 — PROP-01 changed_count · PROP-02 change_type · PROP-04 · 저장 이벤트가 같이 쓴다
(06 제안서 조회 API 「변경」 판단).

같은 항목 코드끼리(값은 영문 코드 — 명세 3.2, 11/10):
  CHANGED 변경 : 판정 한 줄 · 구분 · 다룬 주제 · 목록/표/※ 줄 · 문단 상황 문장 중 하나라도 다름
  NEW · REMOVED 신규 · 빠짐 : 앞 버전에 없음 / 이번에 없음(해당 없음)
  WORDING_ONLY 표현만 : 위가 모두 같고 LLM 할 일 문장만 다름 → 「변경」 아님
  SAME 같음 : 다 같음
DB 없이 proposal_items 행(또는 같은 칸을 가진 객체)만 받는다.
"""

import re
from collections import Counter
from collections.abc import Sequence
from typing import Any, Protocol

from app.db.models.enums import ItemCategory
from rules.items import e1, e2, e3, e4, e5, e6, s1, s2, s3, s4, s5, s6

ITEM_NAMES = {m.ID: m.NAME for m in (s1, s2, s3, s4, s5, s6, e1, e2, e3, e4, e5, e6)}
CODES = list(ITEM_NAMES)  # S1 → S6, E1 → E6
CHANGED = ("CHANGED", "NEW", "REMOVED")  # 「바뀐 항목」으로 세는 것
TABLE_KEYS = ("group", "agency", "ask", "item", "basis")  # rules.items.requests.HEAD 순서
# 「직전 버전 대비」 표기(S1 판정 · 상황 문장, E5 판정): 값이 아니라 비교 기준이 옮겨 간 것
# → 비교할 때만 지운다
RELATIVE = re.compile(
    r", 직전 [^)·]+|\s*직전 회차 .+?에서 (?:올랐|내려갔)습니다\.|\s*/ 바뀐 곳: .*$"
)

Sections = Sequence[dict[str, Any]]
CATEGORY_VALUES = {c.value for c in ItemCategory}


class Item(Protocol):
    item_code: str
    categories: Sequence[Any]
    sections: Sections
    detail: dict[str, Any]


def norm(text: str | None) -> str:
    return RELATIVE.sub("", text or "").strip()


def paragraphs(sections: Sections) -> list[dict[str, Any]]:
    return [b for s in sections for b in s.get("blocks", []) if b.get("type") == "paragraph"]


def table_row(row: dict[str, Any]) -> list[Any]:
    return [row.get(k) for k in TABLE_KEYS]


def rows_of(sections: Sections) -> list[str]:
    """목록 · 표 · ※ 줄을 글 한 줄씩(굵게 표기 뺌)."""
    out = []
    for s in sections:
        for b in s.get("blocks", []):
            if b.get("type") == "list":
                out += [r["text"].replace("**", "") for r in b["rows"]]
            elif b.get("type") == "table":
                out += [" · ".join(str(v) for v in table_row(r) if v) for r in b["rows"]]
            elif b.get("type") == "note":
                out.append(b["text"])
    return out


def category_code(c: Any) -> str:
    """구분 → 영문 코드. ItemCategory · 한글(문단의 「즉시」) 둘 다 받는다."""
    if isinstance(c, ItemCategory):
        return c.name
    return ItemCategory(c).name if c in CATEGORY_VALUES else c


def _codes(item: Item) -> list[str]:
    return [category_code(c) for c in item.categories]


def _engine_side(item: Item) -> tuple[Any, ...]:
    """「변경」을 가르는 값: LLM 할 일 문장을 뺀 전부."""
    ps = paragraphs(item.sections)
    return (
        norm(item.detail.get("finding")),
        _codes(item),
        sorted({str(p.get("topic")) for p in ps}),
        sorted(rows_of(item.sections)),
        [norm(p.get("situation")) for p in ps],
    )


def change_of(before: Item | None, after: Item | None) -> str | None:
    """CHANGED · NEW · REMOVED · WORDING_ONLY · SAME. 둘 다 없으면(해당 없음) None."""
    if before is None and after is None:
        return None
    if before is None:
        return "NEW"
    if after is None:
        return "REMOVED"
    if _engine_side(before) != _engine_side(after):
        return "CHANGED"
    bodies = [[p.get("body") for p in paragraphs(i.sections)] for i in (before, after)]
    return "WORDING_ONLY" if bodies[0] != bodies[1] else "SAME"


def changes(before: Sequence[Item], after: Sequence[Item]) -> dict[str, str]:
    """두 버전의 항목 → {코드: 판단}(둘 다 해당 없음인 코드는 뺌), S1 → E6 순서."""
    a = {i.item_code: i for i in before}
    b = {i.item_code: i for i in after}
    out = {code: change_of(a.get(code), b.get(code)) for code in CODES}
    return {code: c for code, c in out.items() if c is not None}


def changed_count(before: Sequence[Item], after: Sequence[Item]) -> int:
    return sum(c in CHANGED for c in changes(before, after).values())


def _pairs(a: Item | None, b: Item | None) -> list[tuple[dict | None, dict | None]]:
    """주제로 짝 — 같은 주제 여러 문단은 순서대로. 주제 없는 문단은 같은 문장끼리만 짝."""
    pa = paragraphs(a.sections) if a else []
    pb = paragraphs(b.sections) if b else []
    out: list[tuple[dict | None, dict | None]] = []
    for t in dict.fromkeys([p.get("topic") for p in pb + pa]):
        xs = [p for p in pa if p.get("topic") == t]
        ys = [p for p in pb if p.get("topic") == t]
        if t is None:
            xb, yb = [x["body"] for x in xs], [y["body"] for y in ys]
            out += [(x, None) for x in xs if x["body"] not in yb]
            out += [(None, y) for y in ys if y["body"] not in xb]
        else:
            n = max(len(xs), len(ys))
            out += [
                (xs[i] if i < len(xs) else None, ys[i] if i < len(ys) else None) for i in range(n)
            ]
    return out


def _side(p: dict[str, Any] | None) -> dict[str, Any] | None:
    return p and {"category": category_code(p.get("category")), "body": p.get("body")}


def item_diff(a: Item | None, b: Item | None, change: str) -> dict[str, Any]:
    """바뀐 항목 하나(PROP-04 items[]): 판정 한 줄 · 바뀐 제안 · 목록/표 줄 · 실패 안내."""
    fa = a.detail.get("finding") if a else None
    fb = b.detail.get("finding") if b else None
    ra = Counter(rows_of(a.sections)) if a else Counter()
    rb = Counter(rows_of(b.sections)) if b else Counter()
    proposals = [
        {
            "change": "ADDED" if x is None else "REMOVED" if y is None else "MODIFIED",
            "topic": (y or x).get("topic"),
            "before": _side(x),
            "after": _side(y),
        }
        for x, y in _pairs(a, b)
        if not (
            x and y and x.get("body") == y.get("body") and x.get("category") == y.get("category")
        )
    ]
    failed = (
        [
            blk["text"]
            for s in b.sections
            for blk in s.get("blocks", [])
            if blk.get("type") == "failed"
        ]
        if b
        else []
    )
    code = (a or b).item_code
    return {
        "item_code": code,
        "name": (b or a).detail.get("name") or ITEM_NAMES.get(code, ""),
        "change": change,
        "categories": {"before": a and _codes(a), "after": b and _codes(b)},
        "finding": None if a and b and norm(fa) == norm(fb) else {"before": fa, "after": fb},
        "proposals": proposals,
        "rows": {"added": list((rb - ra).elements()), "removed": list((ra - rb).elements())},
        "note": failed[0] if failed else None,
    }
