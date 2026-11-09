"""PROP-01 ~ 04: 저장된 제안서(proposals · proposal_items)를 응답 모양으로(06 제안서 조회 API).

- 한 번 만든 버전은 바뀌지 않는다 → 「변경」은 그 버전의 바로 앞 버전과 비교한 값이라 고정
- 값은 한글(단계 · 계기 · 진화/대피 · 구분 · 상태) — 06 문서 결정. 명세 3.2의 영문 코드와 다름
- 저장하는 칸은 늘지 않는다 — 다 읽을 때 계산
"""

from collections import Counter
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import KST
from app.db.models import Prediction, Proposal, ProposalItem, SituationCorrection
from app.db.models.enums import ItemAxis, ItemCategory, ProposalTrigger, ResponseStage
from app.repositories.proposals import items_by_proposal, previous_proposal
from app.services.proposal_compare import (
    CHANGED,
    CODES,
    ITEM_NAMES,
    change_of,
    changed_count,
    changes,
    item_diff,
    table_row,
)

STAGE_NAME = {
    ResponseStage.INITIAL: "초기 대응",
    ResponseStage.STAGE1: "확산대응 1단계",
    ResponseStage.STAGE2: "확산대응 2단계",
}
STATE_NAME = {"ok": "생성", "partial": "일부 생성", "failed": "실패", "reused": "재사용"}
NOT_TRIGGERED = "해당 없음"
AXIS_NAME = {"S": ItemAxis.SUPPRESSION.value, "E": ItemAxis.EVACUATION.value}
CHANGE_TYPE = {
    "변경": "변경",
    "신규": "신규",
    "표현만": "같음",
    "같음": "같음",
}  # PROP-02 「변경」 태그
HIDDEN = ("quote_ok", "action", "writer")  # 문단의 내부 검사값 · 엔진 자리 번호


def version_of(p: Proposal) -> str:
    return f"v{p.perimeter_version}.{p.seq}"


# --- PROP-01 ---


def category_counts(items: Sequence[ProposalItem]) -> dict[str, int]:
    """구분별 항목 수(한 항목이 여러 구분이면 각각 셈)."""
    n = Counter(c for i in items for c in i.categories)
    return {c.value: n[c] for c in ItemCategory}


def version_rows(session: Session, rows: Sequence[Proposal], limit: int) -> list[dict[str, Any]]:
    """버전 목록 한 쪽. rows = list_proposals 결과(limit + 1개까지)."""
    items = items_by_proposal(session, [p.id for p in rows])
    out = []
    for i, p in enumerate(rows[:limit]):
        prev = rows[i + 1] if i + 1 < len(rows) else None
        out.append(
            {
                "id": p.id,
                "version": version_of(p),
                "trigger": p.trigger.value,
                "created_at": p.created_at,
                "judged_stage": STAGE_NAME[p.judged_stage],
                "predicted_stage": STAGE_NAME[p.predicted_stage],
                "category_counts": category_counts(items[p.id]),
                "changed_count": None
                if prev is None
                else changed_count(items[prev.id], items[p.id]),
            }
        )
    return out


# --- PROP-02 · 03 ---


def _block(b: dict[str, Any]) -> dict[str, Any]:
    if b.get("type") == "table":
        return {"type": "table", "head": b["head"], "rows": [table_row(r) for r in b["rows"]]}
    if b.get("type") == "list":
        return {
            "type": "list",
            "style": b.get("style"),
            "rows": [{"text": r["text"]} for r in b["rows"]],
        }
    return {k: v for k, v in b.items() if k not in HIDDEN}


def sections_out(sections: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """문단의 quote_ok · action을 빼고, 표 행을 열 순서 배열로."""
    return [
        {"title": s.get("title", ""), "blocks": [_block(b) for b in s.get("blocks", [])]}
        for s in sections
    ]


def _item_out(
    code: str, item: ProposalItem | None, prev: dict[str, ProposalItem] | None
) -> dict[str, Any]:
    if item is None:  # 발동 안 한 항목도 칸은 있다
        return {
            "item_code": code,
            "name": ITEM_NAMES[code],
            "axis": AXIS_NAME[code[0]],
            "state": NOT_TRIGGERED,
            "categories": [],
            "finding": "",
            "withheld": [],
            "change_type": None,
            "sections": [],
            "evidence": [],
        }
    change = None if prev is None else change_of(prev.get(code), item)
    return {
        "item_code": code,
        "name": item.detail.get("name") or ITEM_NAMES[code],
        "axis": item.axis.value,
        "state": STATE_NAME.get(item.state, item.state),
        "categories": [c.value for c in item.categories],
        "finding": item.detail.get("finding", ""),
        "withheld": item.detail.get("withheld", []),
        "change_type": CHANGE_TYPE.get(change) if change else None,
        "sections": sections_out(item.sections),
        "evidence": item.evidence,
    }


def proposal_out(session: Session, p: Proposal) -> dict[str, Any]:
    """제안서 하나 → 머리 + 늘 12칸 항목(S1 → E6). PROP-02 · 03이 같이 쓴다."""
    before = previous_proposal(session, p)
    items = items_by_proposal(session, [p.id] + ([before.id] if before else []))
    mine = {i.item_code: i for i in items[p.id]}
    prev = {i.item_code: i for i in items[before.id]} if before else None
    return {
        "id": p.id,
        "version": version_of(p),
        "trigger": p.trigger.value,
        "created_at": p.created_at,
        "base_at": session.get(Prediction, p.prediction_id).base_at,
        "prediction_id": p.prediction_id,
        "judged_stage": STAGE_NAME[p.judged_stage],
        "predicted_stage": STAGE_NAME[p.predicted_stage],
        "items": [_item_out(code, mine.get(code), prev) for code in CODES],
    }


# --- PROP-04 ---


def _hhmm(session: Session, p: Proposal) -> str:
    return session.get(Prediction, p.prediction_id).base_at.astimezone(KST).strftime("%H:%M")


def cause_of(session: Session, before: Proposal, after: Proposal) -> str:
    """「왜 바뀌었나」 한 줄: 상황 정정이면 정정 입력, 예측 갱신이면 새 화선인지."""
    if after.trigger is ProposalTrigger.CORRECTION and after.correction_id is not None:
        text = session.get(SituationCorrection, after.correction_id).input_text
        return f"상황 정정: {text}"
    if after.perimeter_version != before.perimeter_version:
        return f"새 화선(v{after.perimeter_version}, 관측 {_hhmm(session, after)}) 보고로 다시 예측"
    return f"같은 화선에서 다시 예측(예측 기준 {_hhmm(session, after)})"


def diff_out(session: Session, p: Proposal) -> dict[str, Any]:
    """그 버전과 바로 앞 버전 비교 — 바뀐 항목만. 첫 버전이면 빈 결과."""
    head = {
        "version": version_of(p),
        "trigger": p.trigger.value,
        "created_at": p.created_at,
    }
    before = previous_proposal(session, p)
    if before is None:
        return head | {
            "previous_version": None,
            "cause": None,
            "counts": None,
            "items": [],
            "wording_only_items": [],
            "same_items": [],
        }
    items = items_by_proposal(session, [before.id, p.id])
    a = {i.item_code: i for i in items[before.id]}
    b = {i.item_code: i for i in items[p.id]}
    found = changes(items[before.id], items[p.id])
    changed = [item_diff(a.get(c), b.get(c), ch) for c, ch in found.items() if ch in CHANGED]
    wording = [c for c, ch in found.items() if ch == "표현만"]
    same = [c for c, ch in found.items() if ch == "같음"]
    return head | {
        "previous_version": version_of(before),
        "cause": cause_of(session, before, p),
        "counts": {"changed": len(changed), "wording_only": len(wording), "same": len(same)},
        "items": changed,
        "wording_only_items": wording,
        "same_items": same,
    }
