"""산불 · 화선 DTO: Incident · IncidentSummary · Perimeter(명세 4절)와 요청 본문."""

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from app.schemas.common import DateTimeOut, Geometry, Id, In

StatusName = Literal["IN_PROGRESS", "ENDED"]
StageName = Literal["INITIAL", "STAGE1", "STAGE2"]


class IncidentSummaryOut(BaseModel):
    id: Id
    name: str
    status: StatusName
    admin_area_id: Id
    reported_at: DateTimeOut
    current_stage: StageName
    current_perimeter_version: int
    needs_reprediction: bool
    proposal_stale: bool


class IncidentOut(BaseModel):
    """명세의 alert_level은 11/6 DB에서 빠져 응답에 넣지 않는다(04 수정 필요 1번)."""

    id: Id
    name: str
    ignition_point: Geometry
    address: str
    admin_area_id: Id
    started_at: DateTimeOut | None
    reported_at: DateTimeOut
    report_text: str | None
    reporter_org: str | None
    reporter_name: str | None
    reporter_phone: str | None
    status: StatusName
    current_stage: StageName
    situation: dict[str, Any]
    reported_by: Id
    created_at: DateTimeOut
    ended_at: DateTimeOut | None
    ended_by: Id | None
    current_perimeter_id: Id
    current_perimeter_version: int
    latest_prediction_id: Id | None
    latest_proposal_id: Id | None
    needs_reprediction: bool
    proposal_stale: bool
    situation_revision: str
    # 진행 중인 예측 작업(PRED-05로 진행 조회). 화면을 다시 열어도 진행 표시를 이어 간다
    running_operation_id: str | None


class PerimeterOut(BaseModel):
    id: Id
    incident_id: Id
    version_no: int
    geometry: Geometry
    observed_at: DateTimeOut
    reported_by: Id
    created_at: DateTimeOut
    corrected_at: DateTimeOut | None
    area_ha: float


class InitialPerimeterIn(In):
    geometry: Geometry
    observed_at: AwareDatetime


class IncidentCreateIn(In):
    ignition_point: Geometry
    address: str = Field(min_length=1, max_length=200)
    started_at: AwareDatetime | None = None
    reported_at: AwareDatetime
    report_text: str | None = None
    reporter_org: str | None = Field(None, max_length=100)
    reporter_name: str = Field(min_length=1, max_length=50)
    reporter_phone: str = Field(min_length=1, max_length=20)
    # 명세 v0.4의 필수 칸이지만 11/6 DB에서 빠짐 — 보내도 받기만 하고 저장하지 않는다
    alert_level: str | None = None
    initial_perimeter: InitialPerimeterIn | None = None


class IncidentEndIn(In):
    expected_perimeter_id: int = Field(gt=0)


class PerimeterReportIn(In):
    """PER-02 실측 화선 보고."""

    geometry: Geometry  # Polygon · MultiPolygon(EPSG:4326)
    observed_at: AwareDatetime
    expected_perimeter_id: int = Field(gt=0)  # 화면이 본 현재 화선(그사이 바뀌었으면 409)


class PerimeterCorrectionIn(In):
    """PER-03 현재 실측 화선 정정."""

    geometry: Geometry
    observed_at: AwareDatetime
