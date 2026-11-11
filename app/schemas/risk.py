"""위험도 DTO: Risk · RiskFactor(명세 4절)."""

from typing import Literal

from pydantic import BaseModel

from app.schemas.common import DateTimeOut, Id


class RiskFactorOut(BaseModel):
    code: str
    name: str
    axis: str
    raw_value: float | str | None
    grade: float | None  # 1 ~ 5. 면적 · 시각 평균이라 소수일 수 있음(명세는 integer — 협의 필요)
    weight: float
    included: bool
    contribution: float | None
    reason: str | None
    source: str | None
    value_at: DateTimeOut | None


class RiskOut(BaseModel):
    prediction_id: Id
    status: Literal["READY", "PARTIAL", "UNAVAILABLE"]
    score: float | None  # R = √(hazard × exposure)
    hazard: float | None = None  # H(위해): 기상 · 지형 · 연료 · 규모 가중평균
    exposure: float | None = None  # X(노출): 취약계층 · 소방서 거리 · 주변 주택 가중평균
    combine: str = "sqrt_hx"
    grade: str | None
    axes: dict[str, float | None]
    included_weight_sum: float
    factors: list[RiskFactorOut]
    method_version: str | None
    unavailable_reasons: list[str]
