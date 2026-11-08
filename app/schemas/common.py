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
