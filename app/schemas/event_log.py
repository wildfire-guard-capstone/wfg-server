"""이벤트 로그 DTO(명세 4절)."""

from typing import Any

from pydantic import BaseModel

from app.schemas.common import DateTimeOut, Id


class EventLogOut(BaseModel):
    id: Id
    created_at: DateTimeOut
    user_id: Id | None
    incident_id: Id | None
    action: str
    details: dict[str, Any] | None
