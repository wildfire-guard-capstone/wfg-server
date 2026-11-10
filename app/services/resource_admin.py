"""진화자원 · 기지 관리(전산 관리자): RES-03 ~ 05 · BASE-02 ~ 04.

검사 → 바꾸기만 한다. 커밋 · 이벤트 로그는 라우터가 한다.
- 자원: 이름 유일, 기지가 있어야 하고, 투입(DEPLOYED)이면 진행 중인 산불에 연결해야 한다
  (DB 제약 deployed_has_incident). 다른 상태로 바꾸면 연결은 풀린다
- 기지: 이름 유일, 관할은 시도 · 시군구만, 위치는 모를 수 있다(null).
  자원이 남아 있거나 지난 제안서가 가리키는 기지는 지우지 않는다(명세 Q-10)
"""

from typing import Any

from geoalchemy2.shape import from_shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.errors import ApiError, not_found
from app.db.models import Incident, Resource, ResourceBase
from app.db.models._types import SRID
from app.db.models.enums import IncidentStatus, ResourceStatus


def _invalid(field: str, reason: str) -> ApiError:
    return ApiError(
        422, "VALIDATION_ERROR", "입력값을 확인해주세요.", [{"field": field, "reason": reason}]
    )


# ── 자원 ────────────────────────────────────────────────────────────


def get_resource(session: Session, resource_id: int) -> Resource:
    resource = session.get(Resource, resource_id)
    if resource is None:
        raise not_found("진화자원을 찾을 수 없습니다.")
    return resource


def check_attrs(attrs: dict[str, Any]) -> None:
    """capacity_l(담수량 · 물탱크 용량)은 있으면 0 이상 숫자(명세 RES-03)."""
    value = attrs.get("capacity_l")
    if "capacity_l" not in attrs:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise _invalid("attrs.capacity_l", "0 이상 숫자여야 합니다.")


def _check_base(session: Session, base_id: int) -> None:
    if session.get(ResourceBase, base_id) is None:
        raise ApiError(
            422,
            "INVALID_BASE",
            "없는 기지입니다.",
            [{"field": "base_id", "reason": "등록된 기지를 골라야 합니다."}],
        )


def _check_resource_name(session: Session, name: str, except_id: int | None = None) -> None:
    q = select(Resource.id).where(Resource.name == name)
    if except_id is not None:
        q = q.where(Resource.id != except_id)
    if session.scalar(q) is not None:
        raise ApiError(409, "DUPLICATE_RESOURCE_NAME", "이미 있는 자원 이름입니다.")


def _check_deployment(session: Session, status: ResourceStatus, incident_id: int | None) -> None:
    """투입이면 진행 중인 산불이 꼭 있어야 하고, 아니면 산불을 달 수 없다."""
    field = "deployed_incident_id"
    if status is not ResourceStatus.DEPLOYED:
        if incident_id is not None:
            raise _invalid(field, "투입(DEPLOYED) 상태일 때만 산불을 연결합니다.")
        return
    if incident_id is None:
        raise _invalid(field, "투입(DEPLOYED) 상태면 투입된 산불을 골라야 합니다.")
    incident = session.get(Incident, incident_id)
    if incident is None or incident.status is IncidentStatus.ENDED:
        raise _invalid(field, "진행 중인 산불을 골라야 합니다.")


def create_resource(session: Session, values: dict[str, Any]) -> Resource:
    """values: kind · name · base_id · status(enum) · attrs · deployed_incident_id."""
    _check_base(session, values["base_id"])
    _check_resource_name(session, values["name"])
    check_attrs(values["attrs"])
    _check_deployment(session, values["status"], values["deployed_incident_id"])
    resource = Resource(**values)
    session.add(resource)
    session.flush()
    return resource


def update_resource(session: Session, resource: Resource, changes: dict[str, Any]) -> list[str]:
    """보낸 값만 바꾸고 실제로 바뀐 필드 이름을 돌려준다. attrs는 통째로 바꾼다."""
    changes = dict(changes)
    if "base_id" in changes and changes["base_id"] != resource.base_id:
        _check_base(session, changes["base_id"])
    if "name" in changes and changes["name"] != resource.name:
        _check_resource_name(session, changes["name"], resource.id)
    if "attrs" in changes:
        check_attrs(changes["attrs"])
    status = changes.get("status", resource.status)
    if status is ResourceStatus.DEPLOYED:
        incident_id = changes.get("deployed_incident_id", resource.deployed_incident_id)
        if (status, incident_id) != (resource.status, resource.deployed_incident_id):
            _check_deployment(session, status, incident_id)
    else:
        _check_deployment(session, status, changes.get("deployed_incident_id"))
        changes["deployed_incident_id"] = None  # 투입이 아니면 연결을 푼다
    changed = [k for k, v in changes.items() if getattr(resource, k) != v]
    for k in changed:
        setattr(resource, k, changes[k])
    session.flush()
    return changed


def delete_resource(session: Session, resource: Resource) -> None:
    """행을 실제로 지운다(계정 회수와 다름, 명세 RES-05)."""
    session.delete(resource)
    session.flush()


# ── 기지 ────────────────────────────────────────────────────────────


def get_base(session: Session, base_id: int) -> ResourceBase:
    base = session.get(ResourceBase, base_id)
    if base is None:
        raise not_found("기지를 찾을 수 없습니다.")
    return base


def _check_base_name(session: Session, name: str, except_id: int | None = None) -> None:
    q = select(ResourceBase.id).where(ResourceBase.name == name)
    if except_id is not None:
        q = q.where(ResourceBase.id != except_id)
    if session.scalar(q) is not None:
        raise ApiError(409, "DUPLICATE_BASE_NAME", "이미 있는 기지 이름입니다.")


def check_coverage(session: Session, area_ids: list[int]) -> list[int]:
    """관할 = 시도 · 시군구 행만. 중복을 빼고 보낸 순서대로 돌려준다."""
    ids = list(dict.fromkeys(area_ids))
    if not ids:
        return ids
    rows = session.execute(
        text("SELECT id, level::text FROM admin_areas WHERE id = ANY(CAST(:ids AS bigint[]))"),
        {"ids": ids},
    ).all()
    level = dict(rows)
    bad = [i for i in ids if level.get(i) not in ("SIDO", "SIGUNGU")]
    if bad:
        raise ApiError(
            422,
            "INVALID_COVERAGE",
            "관할은 시도 또는 시군구만 고를 수 있습니다.",
            [
                {
                    "field": "coverage_area_ids",
                    "reason": f"없거나 읍면동인 행정구역: {', '.join(str(i) for i in bad)}",
                }
            ],
        )
    return ids


def _geom(point: BaseGeometry | None):
    return from_shape(point, srid=SRID) if point is not None else None


def create_base(
    session: Session,
    *,
    name: str,
    org_type: str,
    coverage_area_ids: list[int],
    point: BaseGeometry | None,
) -> ResourceBase:
    _check_base_name(session, name)
    base = ResourceBase(
        name=name,
        org_type=org_type,
        coverage_area_ids=check_coverage(session, coverage_area_ids),
        geom=_geom(point),
    )
    session.add(base)
    session.flush()
    return base


def update_base(session: Session, base: ResourceBase, changes: dict[str, Any]) -> list[str]:
    """보낸 값만 바꾼다. changes의 위치는 "point"(5179 도형 또는 None), 관할은 통째로 바꾼다."""
    changed = []
    if "name" in changes and changes["name"] != base.name:
        _check_base_name(session, changes["name"], base.id)
        base.name = changes["name"]
        changed.append("name")
    if "org_type" in changes and changes["org_type"] != base.org_type:
        base.org_type = changes["org_type"]
        changed.append("org_type")
    if "coverage_area_ids" in changes:
        ids = check_coverage(session, changes["coverage_area_ids"])
        if ids != list(base.coverage_area_ids):
            base.coverage_area_ids = ids
            changed.append("coverage_area_ids")
    if "point" in changes:
        base.geom = _geom(changes["point"])
        changed.append("geometry")
    session.flush()
    return changed


def delete_base(session: Session, base: ResourceBase) -> None:
    """자원이 남아 있거나 지난 제안서가 가리키면 지우지 않는다. 자원을 따라 지우지 않는다."""
    count = session.scalar(
        select(func.count()).select_from(Resource).where(Resource.base_id == base.id)
    )
    if count:
        raise ApiError(
            409, "BASE_IN_USE", f"이 기지에 자원 {count}대가 있습니다. 먼저 옮기거나 지우세요."
        )
    referenced = session.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM proposal_items"
            " WHERE jsonb_exists_any(detail -> 'targets', CAST(:refs AS text[])))"
        ),
        {"refs": [f"resource_base:{base.id}", f"base:{base.id}"]},
    )
    if referenced:
        raise ApiError(
            409, "HISTORICAL_REFERENCE", "지난 제안서가 이 기지를 가리키고 있어 지울 수 없습니다."
        )
    session.delete(base)
    session.flush()
