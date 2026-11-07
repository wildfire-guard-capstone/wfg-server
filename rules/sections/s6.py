"""S6 · E6 섹션: 요청 표. S6은 대책회의 문단(a1) + 표, E6은 고정 문장 + 표(LLM 안 부름)."""

from typing import Any

from rules.items.requests import HEAD

ANCHOR_IDS = {"p.22": "SM-p022", "p.73": "SM-p073"}
# 표 행 근거 → 근거 청크 ID(근거 검색이 함께 가져옴)
BASIS_IDS = {
    "p.76": ["SM-p076"],
    "p.78": ["SM-p078"],
    "p.79": ["SM-p079"],
    "p.79·96": ["SM-p079", "SM-p096"],
    "p.79·83·95": ["SM-p079", "SM-p083", "SM-p095"],
    "p.89": ["SM-p089"],
    "p.89·95": ["SM-p089", "SM-p095"],
    "p.95": ["SM-p095"],
    "p.96": ["SM-p096"],
    "고시 별표": ["NOTI-MOBL-t0-p1", "NOTI-MOBL-t0-p2"],
}


def _table(rows: list[dict[str, str]]) -> dict[str, Any]:
    return {"type": "table", "writer": "조립기", "head": HEAD, "rows": rows}


def _row_basis(rows: list[dict[str, str]]) -> list[str]:
    return list(dict.fromkeys(i for r in rows for i in BASIS_IDS[r["basis"]]))


def s6_sections(s6: dict[str, Any]) -> dict[str, Any]:
    """S6: a1 대책회의 · 기관별 임무 + 표."""
    blocks = [
        {"type": "action", "id": "S6-a1", "topic": "대책회의 · 기관별 임무",
         "use": ["request_count"], "anchors": ["p.22", "p.73"]},
        _table(s6["rows"]),
    ]  # fmt: skip
    return {
        "sections": [{"title": "", "blocks": blocks}],
        "do_not_state": [w["field"] for w in s6["withheld"]],
        "anchor_basis": list(ANCHOR_IDS.values()) + _row_basis(s6["rows"]),
    }


def e6_sections(e6: dict[str, Any]) -> dict[str, Any]:
    """E6: 고정 문장 「아래 기관에 요청하십시오.」 + 표. action이 없어 LLM을 부르지 않는다."""
    blocks = [
        {"type": "fixed", "writer": "조립기", "text": "아래 기관에 요청하십시오."},
        _table(e6["rows"]),
    ]
    return {
        "sections": [{"title": "", "blocks": blocks}],
        "do_not_state": [],
        "anchor_basis": _row_basis(e6["rows"]),
    }
