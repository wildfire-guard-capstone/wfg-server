# ruff: noqa: E501
"""AI 질의응답 · 상황 정정 읽기 · 쓰기(DB 설계 2.5: qa_messages · situation_corrections)."""

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import QaMessage, SituationCorrection


def add_qa(
    session: Session,
    *,
    incident_id: int,
    user_id: int,
    proposal_id: int | None,
    item_code: str | None,
    question: str,
    answer: str,
    evidence: list[dict[str, Any]],
    detail: dict[str, Any],
) -> QaMessage:
    row = QaMessage(
        incident_id=incident_id,
        user_id=user_id,
        proposal_id=proposal_id,
        item_code=item_code,
        question=question,
        answer=answer,
        evidence=evidence,
        detail=detail,
    )
    session.add(row)
    session.flush()
    return row


def list_qa(
    session: Session, incident_id: int, *, before_id: int | None, limit: int
) -> list[QaMessage]:
    """id 내림차순, limit + 1개(명세 AI-02)."""
    q = (
        select(QaMessage)
        .where(QaMessage.incident_id == incident_id)
        .order_by(QaMessage.id.desc())
        .limit(limit + 1)
    )
    if before_id is not None:
        q = q.where(QaMessage.id < before_id)
    return list(session.scalars(q))


def add_correction(
    session: Session,
    *,
    incident_id: int,
    user_id: int,
    input_text: str,
    changes: list[dict[str, Any]],
) -> SituationCorrection:
    row = SituationCorrection(
        incident_id=incident_id, user_id=user_id, input_text=input_text, changes=changes
    )
    session.add(row)
    session.flush()
    return row


def list_corrections(
    session: Session, incident_id: int, *, before_id: int | None, limit: int
) -> list[SituationCorrection]:
    """id 내림차순, limit + 1개(명세 COR-02)."""
    q = (
        select(SituationCorrection)
        .where(SituationCorrection.incident_id == incident_id)
        .order_by(SituationCorrection.id.desc())
        .limit(limit + 1)
    )
    if before_id is not None:
        q = q.where(SituationCorrection.id < before_id)
    return list(session.scalars(q))


def village_directory(session: Session, sigungu_id: int | None) -> list[dict[str, Any]]:
    """정정 대상 마을: 산불 시군구 + 인접 시군구의 리(id · 이름 · 읍면 · 시군구). 산불 시군구가 먼저."""
    if sigungu_id is None:
        return []
    rows = session.execute(
        text(
            "SELECT v.id, v.name, e.name, s.name, s.id = :sgg AS home"
            " FROM villages v JOIN admin_areas e ON e.id = v.admin_area_id"
            " JOIN admin_areas s ON s.id = e.parent_id"
            " WHERE s.id = :sgg OR s.id IN"
            "   (SELECT unnest(neighbor_ids) FROM admin_areas WHERE id = :sgg)"
            " ORDER BY home DESC, v.id"
        ),
        {"sgg": sigungu_id},
    ).all()
    return [
        {"id": vid, "name": name, "emd": emd, "sigungu": sgg, "home": home}
        for vid, name, emd, sgg, home in rows
    ]
