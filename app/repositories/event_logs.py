"""이벤트 로그(DB 설계 2.1): 추가만 한다. details에 개인정보 · 비밀번호 · 토큰을 넣지 않는다."""

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import EventLog


def add(
    session: Session,
    action: str,
    *,
    user_id: int | None = None,
    incident_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(EventLog(action=action, user_id=user_id, incident_id=incident_id, details=details))
