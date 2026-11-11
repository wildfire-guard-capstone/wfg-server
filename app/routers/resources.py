"""RES-01 진화자원 목록 · RES-02 상태 집계 · BASE-01 기지 목록,
전산 관리자: RES-03 ~ 05 자원 등록 · 수정 · 삭제 · BASE-02 ~ 04 기지 등록 · 수정 · 삭제."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response
from geoalchemy2.shape import to_shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy.orm import Session

from app.api.deps import Db, require_roles
from app.api.envelope import Envelope, Page, ok
from app.api.errors import ApiError
from app.api.geo import from_geojson, to_geojson
from app.api.pagination import LIMIT, cut, decode_cursor
from app.db.models import Incident, Resource, ResourceBase, User
from app.db.models.enums import ResourceKind, ResourceStatus, UserRole
from app.repositories import event_logs, incidents, resources
from app.routers.incidents import load_incident
from app.schemas.resource import (
    BaseCreateIn,
    BaseUpdateIn,
    KindName,
    ResourceBaseOut,
    ResourceCount,
    ResourceCreateIn,
    ResourceOut,
    ResourceStatusName,
    ResourceSummary,
    ResourceUpdateIn,
)
from app.services import resource_admin

router = APIRouter(tags=["진화자원"])
Reader = require_roles("COMMANDER", "VIEWER", "ADMIN")
Admin = require_roles("ADMIN")
ResourceId = Annotated[int, Path(gt=0)]
BaseId = Annotated[int, Path(gt=0)]
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


def resource_out(r: Resource) -> ResourceOut:
    return ResourceOut(
        id=r.id,
        kind=r.kind.name,
        name=r.name,
        base_id=r.base_id,
        status=r.status.name,
        attrs=r.attrs,
        deployed_incident_id=r.deployed_incident_id,
    )


def base_out(
    b: ResourceBase, origin: BaseGeometry | None = None, home: set[int] | None = None
) -> ResourceBaseOut:
    """origin = 산불 발화점, home = 산불의 시군구 · 시도. 산불 문맥이 없으면 둘 다 None →
    거리 · 관할 구분은 null(관리 API 응답도 이 모양)."""
    point = to_shape(b.geom) if b.geom is not None else None
    return ResourceBaseOut(
        id=b.id,
        name=b.name,
        org_type=b.org_type,
        geometry=to_geojson(point) if point is not None else None,
        coverage_area_ids=b.coverage_area_ids,
        distance_km=round(point.distance(origin) / 1000, 1)
        if (point is not None and origin is not None)
        else None,
        jurisdiction=None
        if home is None
        else ("LOCAL" if home & set(b.coverage_area_ids) else "SUPPORT"),
    )


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
    out = [resource_out(r) for r in items]
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
    out = [base_out(b, origin, home if incident else None) for b in items]
    return ok(request, Page(items=out, next_cursor=next_cursor, has_more=has_more))


# ── 전산 관리자: 자원 · 기지 관리 ───────────────────────────────────────


def _log(db: Session, admin: User, action: str, op: str, **details) -> None:
    """등록 · 수정 · 삭제 모두 같은 이벤트 이름에 op로 구분한다(명세 RES-03)."""
    event_logs.add(db, action, user_id=admin.id, details={"op": op, **details})


@router.post("/admin/resources", status_code=201, response_model=Envelope[ResourceOut])
def create_resource(body: ResourceCreateIn, request: Request, admin: Admin, db: Db):
    """RES-03. 헬기 · 차량 1대 등록."""
    values = body.model_dump()
    values["kind"] = ResourceKind[body.kind]
    values["status"] = ResourceStatus[body.status]
    resource = resource_admin.create_resource(db, values)
    _log(db, admin, "RESOURCE_UPDATED", "created", resource_id=resource.id, name=resource.name)
    db.commit()
    return ok(request, resource_out(resource))


@router.patch("/admin/resources/{resource_id}", response_model=Envelope[ResourceOut])
def update_resource(
    resource_id: ResourceId, body: ResourceUpdateIn, request: Request, admin: Admin, db: Db
):
    """RES-04. 보낸 값만 바꾼다. attrs는 통째로 바뀌고,
    투입이 아닌 상태가 되면 산불 연결이 풀린다."""
    resource = resource_admin.get_resource(db, resource_id)
    changes = body.model_dump(exclude_unset=True)
    if "kind" in changes:
        changes["kind"] = ResourceKind[changes["kind"]]
    if "status" in changes:
        changes["status"] = ResourceStatus[changes["status"]]
    changed = resource_admin.update_resource(db, resource, changes)
    if changed:
        _log(
            db,
            admin,
            "RESOURCE_UPDATED",
            "updated",
            resource_id=resource.id,
            name=resource.name,
            changed=changed,
        )
    db.commit()
    return ok(request, resource_out(resource))


@router.delete("/admin/resources/{resource_id}", status_code=204)
def delete_resource(resource_id: ResourceId, admin: Admin, db: Db):
    """RES-05. 행을 실제로 지운다. 없는 id는 404(다시 지워도 404)."""
    resource = resource_admin.get_resource(db, resource_id)
    details = {"resource_id": resource.id, "name": resource.name}
    resource_admin.delete_resource(db, resource)
    _log(db, admin, "RESOURCE_UPDATED", "deleted", **details)
    db.commit()
    return Response(status_code=204)


def _point(geometry: dict | None) -> BaseGeometry | None:
    return from_geojson(geometry, ("Point",), "geometry") if geometry is not None else None


@router.post("/admin/resource-bases", status_code=201, response_model=Envelope[ResourceBaseOut])
def create_base(body: BaseCreateIn, request: Request, admin: Admin, db: Db):
    """BASE-02. 관할은 시도 · 시군구만, 위치는 모르면 null."""
    base = resource_admin.create_base(
        db,
        name=body.name,
        org_type=body.org_type,
        coverage_area_ids=body.coverage_area_ids,
        point=_point(body.geometry),
    )
    _log(db, admin, "BASE_UPDATED", "created", base_id=base.id, name=base.name)
    db.commit()
    return ok(request, base_out(base))


@router.patch("/admin/resource-bases/{base_id}", response_model=Envelope[ResourceBaseOut])
def update_base(base_id: BaseId, body: BaseUpdateIn, request: Request, admin: Admin, db: Db):
    """BASE-03. 보낸 값만 바꾼다. 관할 목록은 통째로 바뀌고, geometry: null이면 위치 미상이 된다.
    지난 제안서에 저장된 값은 그대로다(제안서가 그때 값을 따로 갖고 있음)."""
    base = resource_admin.get_base(db, base_id)
    changes = body.model_dump(exclude_unset=True)
    if "geometry" in changes:
        changes["point"] = _point(changes.pop("geometry"))
    changed = resource_admin.update_base(db, base, changes)
    if changed:
        _log(db, admin, "BASE_UPDATED", "updated", base_id=base.id, name=base.name, changed=changed)
    db.commit()
    return ok(request, base_out(base))


@router.delete("/admin/resource-bases/{base_id}", status_code=204)
def delete_base(base_id: BaseId, admin: Admin, db: Db):
    """BASE-04. 자원이 남아 있으면 409 BASE_IN_USE,
    지난 제안서가 가리키면 409 HISTORICAL_REFERENCE."""
    base = resource_admin.get_base(db, base_id)
    details = {"base_id": base.id, "name": base.name}
    resource_admin.delete_base(db, base)
    _log(db, admin, "BASE_UPDATED", "deleted", **details)
    db.commit()
    return Response(status_code=204)
