"""예측 DTO: Prediction · Operation(명세 4절), 예측 요청 본문."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import DateTimeOut, Id, In
from app.services.operations import Operation


class SliceSummary(BaseModel):
    hour: int
    at: DateTimeOut
    area_ha: float


class PredictionOut(BaseModel):
    id: Id
    incident_id: Id
    perimeter_id: Id
    perimeter_version: int
    base_at: DateTimeOut
    requested_by: Id
    created_at: DateTimeOut
    slices: list[SliceSummary]


class OperationOut(BaseModel):
    id: str
    kind: Literal["PREDICTION", "PROPOSAL_REGENERATION"]
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    stage: str | None
    result: dict[str, Any] | None
    error: dict[str, str] | None

    @classmethod
    def of(cls, op: Operation) -> "OperationOut":
        return cls(
            id=op.id,
            kind=op.kind,
            status=op.status,
            stage=op.stage,
            result=op.result,
            error=op.error,
        )


# 예측 모드(PRED-01). 화면 · Postman에서 고른다. 없으면 서버 기본 설정(지금은 시험용 가짜 예측)
#   LIVE          실서비스: 의성 덱 땅 + 실시간 기상(관측소 매분자료 + 단기예보) + ELMFIRE
#   DEMO_ELMFIRE  시연 1: 의성 덱 땅 + 덱에 든 2025-03-22 ~ 28 실측 기상 + ELMFIRE
#   (DEMO_REPLAY  시연 2: 재현 예측 — 만들면 더하고 기본값으로)
PredictionMode = Literal["LIVE", "DEMO_ELMFIRE"]


class PredictionRequestIn(In):
    expected_perimeter_id: int = Field(gt=0)
    mode: PredictionMode | None = None
