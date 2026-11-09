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


class PredictionRequestIn(In):
    expected_perimeter_id: int = Field(gt=0)
