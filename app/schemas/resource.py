"""진화자원 DTO: Resource · ResourceBase(명세 4절), 상태 집계."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import Geometry, Id, In, need_one, no_nulls

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


class ResourceCreateIn(In):
    """RES-03. 헬기 · 차량 1대. 투입(DEPLOYED)이면 deployed_incident_id가 꼭 있어야 한다."""

    kind: KindName
    name: str = Field(min_length=1, max_length=50)
    base_id: int = Field(gt=0)
    status: ResourceStatusName = "AVAILABLE"
    attrs: dict[str, Any] = Field(default_factory=dict)
    deployed_incident_id: int | None = Field(None, gt=0)


class ResourceUpdateIn(In):
    """RES-04. 보낸 것만 바꾼다(1개 이상). deployed_incident_id만 null을 받는다."""

    kind: KindName | None = None
    name: str | None = Field(None, min_length=1, max_length=50)
    base_id: int | None = Field(None, gt=0)
    status: ResourceStatusName | None = None
    attrs: dict[str, Any] | None = None
    deployed_incident_id: int | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _check(self) -> "ResourceUpdateIn":
        need_one(self)
        no_nulls(self, "kind", "name", "base_id", "status", "attrs")
        return self


class BaseCreateIn(In):
    """BASE-02. 위치(geometry)는 Point 또는 null — 모르는 기지도 등록한다."""

    name: str = Field(min_length=1, max_length=100)
    org_type: str = Field(min_length=1, max_length=20)
    coverage_area_ids: list[int]
    geometry: Geometry | None = None


class BaseUpdateIn(In):
    """BASE-03. 보낸 것만 바꾼다(1개 이상). geometry만 null을 받고, 관할 목록은 통째로 바꾼다."""

    name: str | None = Field(None, min_length=1, max_length=100)
    org_type: str | None = Field(None, min_length=1, max_length=20)
    coverage_area_ids: list[int] | None = None
    geometry: Geometry | None = None

    @model_validator(mode="after")
    def _check(self) -> "BaseUpdateIn":
        need_one(self)
        no_nulls(self, "name", "org_type", "coverage_area_ids")
        return self
