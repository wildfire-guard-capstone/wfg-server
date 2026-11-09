"""결과 조회 API 응답 형식. 이름 · 값은 DB 설계(app.db.models)를 따른다.

열거형은 값(한국어 이름)으로 주고받는다 — 예: ``"확산대응 1단계"``, ``"진화"``, ``"협의"``.
좌표는 GeoJSON geometry(EPSG:4326, [경도, 위도]), 시각은 ISO 8601(+09:00).
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.db.models.enums import (
    IncidentStatus,
    ItemAxis,
    ItemCategory,
    ProposalTrigger,
    ResponseStage,
)

Geometry = dict[str, Any]


class IncidentSummary(BaseModel):
    id: int
    name: str
    address: str
    status: IncidentStatus
    current_stage: ResponseStage
    started_at: datetime | None
    reported_at: datetime
    latest_perimeter_version: int | None
    latest_proposal_version: str | None


class Incident(IncidentSummary):
    sigungu: str
    emd: str
    ignition_point: Geometry


class Perimeter(BaseModel):
    """실측 화선 1건. v0 = 발화점(Point), v1 이상 = 연소 범위(MultiPolygon)."""

    version_no: int
    observed_at: datetime
    report: str | None
    area_ha: float | None
    geometry: Geometry


class PredictionSlice(BaseModel):
    """누적 확산 범위 Pn(n = 1~8). 시각 = 예측 기준 시각 + n시간.

    P5 = 위험구역, P8 = 잠재 위험구역.
    """

    hour: int
    at: datetime
    area_ha: float
    geometry: Geometry


class WeatherHour(BaseModel):
    hour: int
    wind_ms: float
    wind_deg: float


class Calibration(BaseModel):
    method: str
    adj: float
    basis: str


class Validation(BaseModel):
    """뒤 시각의 실측 화선과 비교(데모 검증용)."""

    at: datetime
    hours: float
    pred_ha: float
    obs_ha: float
    iou: float


class PredictionSummary(BaseModel):
    id: int
    perimeter_version: int
    base_at: datetime
    areas_ha: list[float]


class Prediction(PredictionSummary):
    calibration: Calibration | None
    weather: list[WeatherHour]
    slices: list[PredictionSlice]
    validation: list[Validation]


class Commander(BaseModel):
    now: str
    next: str | None


class Evidence(BaseModel):
    chunk_id: str
    label: str
    doc_name: str
    section: str | None
    page: str | None
    origin: str | None
    text: str


class Withheld(BaseModel):
    field: str
    reason: str


class Section(BaseModel):
    """``blocks``: paragraph(category · text · evidence · quote) · list · table · note · failed."""

    title: str
    blocks: list[dict[str, Any]]


class ProposalItem(BaseModel):
    code: str
    axis: ItemAxis
    name: str
    state: Literal["ok", "partial", "failed"]
    finding: str | None
    categories: list[ItemCategory]
    sections: list[Section]
    evidence: list[Evidence]
    withheld: list[Withheld]


class ProposalSummary(BaseModel):
    version: str
    perimeter_version: int
    seq: int
    prediction_id: int
    trigger: ProposalTrigger
    reader: str
    commander: Commander
    judged_stage: ResponseStage
    predicted_stage: ResponseStage
    base_at: datetime
    created_at: datetime
    item_states: dict[str, str]


class Proposal(ProposalSummary):
    items: list[ProposalItem]
