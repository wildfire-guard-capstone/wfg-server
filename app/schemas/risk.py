"""위험도 DTO: Risk · RiskFactor(명세 4절)."""

from typing import Literal

from pydantic import BaseModel

from app.schemas.common import DateTimeOut, Id


class RiskFactorOut(BaseModel):
    code: str
    name: str
    axis: str
    raw_value: float | str | None
    grade: int | None
    weight: float
    included: bool
    contribution: float | None
    reason: str | None
    source: str | None
    value_at: DateTimeOut | None


class RiskOut(BaseModel):
    prediction_id: Id
    status: Literal["READY", "PARTIAL", "UNAVAILABLE"]
    score: float | None
    grade: str | None
    axes: dict[str, float | None]
    included_weight_sum: float
    factors: list[RiskFactorOut]
    method_version: str | None
    unavailable_reasons: list[str]
