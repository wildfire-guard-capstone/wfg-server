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


# 투입 칸 ↔ 자원 테이블(resources.kind · resource_bases.org_type). 군 · 경찰 · 미상 · 기타 · 인력은 자원
# 테이블에 없어 상한을 두지 않는다(11-10 결정: 상한은 자원 테이블 기준)
CAPACITY_SLOTS = {
    "helicopter.forest": ("HELICOPTER", ("forest",)),
    "helicopter.local": ("HELICOPTER", ("local_lease", "local")),
    "helicopter.fire": ("HELICOPTER", ("fire",)),
    "vehicle.forest_vehicle": ("VEHICLE", ("forest", "local_lease", "local")),
    "vehicle.fire_truck": ("VEHICLE", ("fire",)),
}


def capacity(session: Session, incident_id: int) -> dict[str, int]:
    """투입 칸별 상한 = 자원 관리자가 올린 자원 중 대기 + 이 산불에 이미 투입된 대수.

    정비 중 · 다른 산불에 투입된 것은 빼고, 그 칸의 자원이 하나도 등록되지 않았으면 상한을 두지 않는다
    (자원 자료가 없는 DB에서 모든 정정을 막지 않게).
    """
    rows = session.execute(
        text(
            "SELECT r.kind::text, b.org_type, count(*),"
            " count(*) FILTER (WHERE r.status = 'AVAILABLE' OR r.deployed_incident_id = :inc)"
            " FROM resources r JOIN resource_bases b ON b.id = r.base_id GROUP BY 1, 2"
        ),
        {"inc": incident_id},
    ).all()
    out = {}
    for slot, (kind, orgs) in CAPACITY_SLOTS.items():
        mine = [(n, usable) for k, org, n, usable in rows if k == kind and org in orgs]
        if sum(n for n, _ in mine):
            out[slot] = sum(usable for _, usable in mine)
    return out


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
