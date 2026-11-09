"""진화자원 · 기지 읽기(DB 설계 2.6)."""

from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models import Resource, ResourceBase
from app.db.models.enums import ResourceKind, ResourceStatus


def list_resources(
    session: Session,
    *,
    kind: ResourceKind | None,
    status: ResourceStatus | None,
    base_id: int | None,
    after_id: int | None,
    limit: int,
) -> list[Resource]:
    q = select(Resource).order_by(Resource.id).limit(limit + 1)
    if kind is not None:
        q = q.where(Resource.kind == kind)
    if status is not None:
        q = q.where(Resource.status == status)
    if base_id is not None:
        q = q.where(Resource.base_id == base_id)
    if after_id is not None:
        q = q.where(Resource.id > after_id)
    return list(session.scalars(q))


def counts_by_base(
    session: Session, *, kind: ResourceKind | None, base_id: int | None
) -> list[dict[str, Any]]:
    """기지 · 종류별 대기 · 투입 · 정비 · 합계(상태 거르기 없이 전부)."""
    q = (
        select(
            Resource.base_id,
            Resource.kind,
            func.count().filter(Resource.status == ResourceStatus.AVAILABLE),
            func.count().filter(Resource.status == ResourceStatus.DEPLOYED),
            func.count().filter(Resource.status == ResourceStatus.MAINTENANCE),
            func.count(),
        )
        .group_by(Resource.base_id, Resource.kind)
        .order_by(Resource.base_id, Resource.kind)
    )
    if kind is not None:
        q = q.where(Resource.kind == kind)
    if base_id is not None:
        q = q.where(Resource.base_id == base_id)
    keys = ("base_id", "kind", "available", "deployed", "maintenance", "total")
    return [dict(zip(keys, row, strict=True)) for row in session.execute(q)]


def linked_counts(session: Session, incident_id: int) -> dict[str, int]:
    """이 산불에 연결된(투입) 헬기 · 차량 수(resources.deployed_incident_id, 11/6)."""
    rows = session.execute(
        select(Resource.kind, func.count())
        .where(Resource.deployed_incident_id == incident_id)
        .group_by(Resource.kind)
    )
    out = {k.name: 0 for k in ResourceKind}
    out.update({k.name: n for k, n in rows})
    return out


def list_bases(
    session: Session, *, org_type: str | None, after_id: int | None, limit: int
) -> list[ResourceBase]:
    q = select(ResourceBase).order_by(ResourceBase.id).limit(limit + 1)
    if org_type is not None:
        q = q.where(ResourceBase.org_type == org_type)
    if after_id is not None:
        q = q.where(ResourceBase.id > after_id)
    return list(session.scalars(q))


def sido_of(session: Session, sigungu_id: int) -> int | None:
    return session.scalar(
        text("SELECT parent_id FROM admin_areas WHERE id = :id"), {"id": sigungu_id}
    )
