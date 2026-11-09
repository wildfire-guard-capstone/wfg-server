"""RES-01 진화자원 목록 · RES-02 상태 집계 · BASE-01 기지 목록."""

from fastapi import APIRouter, Query, Request
from geoalchemy2.shape import to_shape
from sqlalchemy.orm import Session

from app.api.deps import Db, require_roles
from app.api.envelope import Envelope, Page, ok
from app.api.errors import ApiError
from app.api.geo import to_geojson
from app.api.pagination import LIMIT, cut, decode_cursor
from app.db.models import Incident, User
from app.db.models.enums import ResourceKind, ResourceStatus, UserRole
from app.repositories import incidents, resources
from app.routers.incidents import load_incident
from app.schemas.resource import (
    KindName,
    ResourceBaseOut,
    ResourceCount,
    ResourceOut,
    ResourceStatusName,
    ResourceSummary,
)

router = APIRouter(tags=["진화자원"])
Reader = require_roles("COMMANDER", "VIEWER", "ADMIN")
IncidentQuery = Query(None, gt=0, description="지휘권자 · 열람자는 필수(산불 문맥)")


def incident_context(db: Session, user: User, incident_id: int | None) -> Incident | None:
    """지휘권자 · 열람자는 산불을 꼭 골라야 하고(관할 확인), 전산 관리자는 생략 가능."""
    if incident_id is None:
        if user.role is not UserRole.ADMIN:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "입력값을 확인해주세요.",
                [{"field": "incident_id", "reason": "산불을 골라야 합니다."}],
            )
        return None
    return load_incident(db, user, incident_id)  # 전산 관리자는 관할이 없어 모든 산불


@router.get("/resources", response_model=Envelope[Page[ResourceOut]])
def list_resources(
    request: Request,
    user: Reader,
    db: Db,
    incident_id: int | None = IncidentQuery,
    kind: KindName | None = None,
    status: ResourceStatusName | None = None,
    base_id: int | None = Query(None, gt=0),
    cursor: str | None = None,
    limit: int = LIMIT,
):
    """RES-01. id 순. 관할 · 응원 범위로 줄이는 것은 명세 Q-08 뒤(지금은 전부)."""
    incident_context(db, user, incident_id)
    after = decode_cursor(cursor, 1)
    rows = resources.list_resources(
        db,
        kind=ResourceKind[kind] if kind else None,
        status=ResourceStatus[status] if status else None,
        base_id=base_id,
        after_id=int(after[0]) if after else None,
        limit=limit,
    )
    items, next_cursor, has_more = cut(rows, limit, lambda r: (r.id,))
    out = [
        ResourceOut(
            id=r.id,
            kind=r.kind.name,
            name=r.name,
            base_id=r.base_id,
            status=r.status.name,
            attrs=r.attrs,
            deployed_incident_id=r.deployed_incident_id,
        )
        for r in items
    ]
    return ok(request, Page(items=out, next_cursor=next_cursor, has_more=has_more))


@router.get("/resources/summary", response_model=Envelope[ResourceSummary])
def resource_summary(
    request: Request,
    user: Reader,
    db: Db,
    incident_id: int | None = IncidentQuery,
    kind: KindName | None = None,
    base_id: int | None = Query(None, gt=0),
):
    """RES-02. 기지 · 종류별 상태 전부. 산불을 고르면 그 산불의 투입 수도."""
    incident = incident_context(db, user, incident_id)
    counts = resources.counts_by_base(
        db, kind=ResourceKind[kind] if kind else None, base_id=base_id
    )
    bases = [ResourceCount(**{**c, "kind": c["kind"].name}) for c in counts]
    summary = ResourceSummary(
        bases=bases,
        incident_deployed=incident.situation.get("deployed") if incident else None,
        incident_linked=resources.linked_counts(db, incident.id) if incident else None,
    )
    return ok(request, summary)


@router.get("/resource-bases", response_model=Envelope[Page[ResourceBaseOut]])
def list_bases(
    request: Request,
    user: Reader,
    db: Db,
    incident_id: int | None = IncidentQuery,
    org_type: str | None = Query(None, max_length=20),
    cursor: str | None = None,
    limit: int = LIMIT,
):
    """BASE-01. 산불을 고르면 발화점까지 직선거리 · 관할(LOCAL)/응원(SUPPORT). 위치 미상은 null."""
    incident = incident_context(db, user, incident_id)
    origin = to_shape(incident.ignition_point) if incident else None
    home: set[int] = set()
    if incident:
        sigungu = incidents.sigungu_of(db, incident)
        home = {sigungu, resources.sido_of(db, sigungu)} - {None}
    after = decode_cursor(cursor, 1)
    rows = resources.list_bases(
        db, org_type=org_type, after_id=int(after[0]) if after else None, limit=limit
    )
    items, next_cursor, has_more = cut(rows, limit, lambda b: (b.id,))
    out = []
    for b in items:
        point = to_shape(b.geom) if b.geom is not None else None
        out.append(
            ResourceBaseOut(
                id=b.id,
                name=b.name,
                org_type=b.org_type,
                geometry=to_geojson(point) if point is not None else None,
                coverage_area_ids=b.coverage_area_ids,
                distance_km=round(point.distance(origin) / 1000, 1)
                if (point is not None and origin is not None)
                else None,
                jurisdiction=(
                    ("LOCAL" if home & set(b.coverage_area_ids) else "SUPPORT")
                    if incident
                    else None
                ),
            )
        )
    return ok(request, Page(items=out, next_cursor=next_cursor, has_more=has_more))
