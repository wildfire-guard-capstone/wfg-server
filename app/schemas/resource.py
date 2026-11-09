"""진화자원 DTO: Resource · ResourceBase(명세 4절), 상태 집계."""

from typing import Any, Literal

from pydantic import BaseModel

from app.schemas.common import Geometry, Id

KindName = Literal["HELICOPTER", "VEHICLE"]
ResourceStatusName = Literal["AVAILABLE", "DEPLOYED", "MAINTENANCE"]


class ResourceOut(BaseModel):
    id: Id
    kind: KindName
    name: str
    base_id: Id
    status: ResourceStatusName
    attrs: dict[str, Any]
    # 명세 v0.4에는 없음 — 11/6 DB에 생긴 「투입된 산불」(04 수정 필요 2번)
    deployed_incident_id: Id | None


class ResourceCount(BaseModel):
    base_id: Id
    kind: KindName
    available: int
    deployed: int
    maintenance: int
    total: int


class ResourceSummary(BaseModel):
    bases: list[ResourceCount]
    incident_deployed: dict[str, Any] | None  # 명세: 그 산불의 Situation.deployed(상황 보고 숫자)
    # 명세 v0.4에는 없음 — 자원 연결(deployed_incident_id)로 센 헬기 · 차량 수(04 수정 필요 2번)
    incident_linked: dict[str, int] | None


class ResourceBaseOut(BaseModel):
    id: Id
    name: str
    org_type: str
    geometry: Geometry | None
    coverage_area_ids: list[Id]
    distance_km: float | None
    jurisdiction: Literal["LOCAL", "SUPPORT"] | None
