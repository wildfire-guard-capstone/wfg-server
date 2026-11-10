"""DTO 공통 타입: ID = 10진수 문자열, 시각 = 시간대 포함 ISO8601(한국 시간으로 보냄)."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.core.time import KST

Id = Annotated[int, PlainSerializer(lambda v: str(v), return_type=str)]
DateTimeOut = Annotated[
    datetime, PlainSerializer(lambda v: v.astimezone(KST).isoformat(), return_type=str)
]
Geometry = dict[str, Any]  # GeoJSON geometry, EPSG:4326, [경도, 위도]


class In(BaseModel):
    """요청 본문. 명시하지 않은 필드는 422(명세 2.1)."""

    model_config = ConfigDict(extra="forbid")


def need_one(body: BaseModel) -> None:
    """수정 요청: 필드를 하나도 안 보냈으면 422."""
    if not body.model_fields_set:
        raise ValueError("바꿀 값을 하나 이상 보내야 합니다.")


def no_nulls(body: BaseModel, *fields: str) -> None:
    """수정 요청: null을 받지 않는 필드에 null을 보냈으면 422(안 보내는 것과 다르다)."""
    for name in fields:
        if name in body.model_fields_set and getattr(body, name) is None:
            raise ValueError(f"{name}은(는) null로 바꿀 수 없습니다.")
