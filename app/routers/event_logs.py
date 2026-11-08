"""LOG-01 이벤트 로그(읽기 전용)."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import Db, no_store, require_roles
from app.api.envelope import Envelope, Page, ok
from app.api.errors import ApiError
from app.api.pagination import LIMIT, cut, decode_cursor
from app.db.models.enums import UserRole
from app.repositories import event_logs
from app.routers.incidents import load_incident
from app.schemas.event_log import EventLogOut

router = APIRouter(prefix="/event-logs", tags=["이벤트 로그"], dependencies=[Depends(no_store)])
Reader = require_roles("COMMANDER", "ADMIN")


@router.get("", response_model=Envelope[Page[EventLogOut]])
def list_logs(
    request: Request,
    user: Reader,
    db: Db,
    incident_id: int | None = Query(None, gt=0, description="지휘권자는 필수"),
    action: str | None = Query(None, max_length=50),
    user_id: int | None = Query(None, gt=0),
    cursor: str | None = None,
    limit: int = LIMIT,
):
    """LOG-01. 최신순. 지휘권자 = 관할 산불의 로그, 전산 관리자 = 산불을 안 고르면
    인증 · 계정 · 자원 관리 로그(산불과 상관없는 것)."""
    if incident_id is None and user.role is UserRole.COMMANDER:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "입력값을 확인해주세요.",
            [{"field": "incident_id", "reason": "산불을 골라야 합니다."}],
        )
    if incident_id is not None:
        load_incident(db, user, incident_id)
    after = decode_cursor(cursor, 2)
    rows = event_logs.list_logs(
        db,
        incident_id=incident_id,
        system_only=incident_id is None,
        action=action,
        user_id=user_id,
        after=(datetime.fromisoformat(after[0]), int(after[1])) if after else None,
        limit=limit,
    )
    items, next_cursor, has_more = cut(rows, limit, lambda e: (e.created_at.isoformat(), e.id))
    out = [
        EventLogOut(
            id=e.id,
            created_at=e.created_at,
            user_id=e.user_id,
            incident_id=e.incident_id,
            action=e.action,
            details=e.details,
        )
        for e in items
    ]
    return ok(request, Page(items=out, next_cursor=next_cursor, has_more=has_more))
