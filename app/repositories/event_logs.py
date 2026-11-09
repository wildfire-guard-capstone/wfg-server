"""이벤트 로그(DB 설계 2.1): 추가만 한다. details에 개인정보 · 비밀번호 · 토큰을 넣지 않는다."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
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


def list_logs(
    session: Session,
    *,
    incident_id: int | None,
    system_only: bool,
    action: str | None,
    user_id: int | None,
    after: tuple[datetime, int] | None,
    limit: int,
) -> list[EventLog]:
    """최신순(시각 · id 내림차순), limit + 1개.

    system_only = 산불과 상관없는 로그(인증 · 계정 · 자원 관리)만.
    """
    q = select(EventLog).order_by(EventLog.created_at.desc(), EventLog.id.desc()).limit(limit + 1)
    if incident_id is not None:
        q = q.where(EventLog.incident_id == incident_id)
    elif system_only:
        q = q.where(EventLog.incident_id.is_(None))
    if action is not None:
        q = q.where(EventLog.action == action)
    if user_id is not None:
        q = q.where(EventLog.user_id == user_id)
    if after is not None:
        at, last = after
        q = q.where(
            (EventLog.created_at < at) | ((EventLog.created_at == at) & (EventLog.id < last))
        )
    return list(session.scalars(q))
