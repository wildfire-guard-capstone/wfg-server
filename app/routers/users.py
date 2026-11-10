"""USR-01 계정 목록 · USR-02 계정 발급(전산 관리자)."""

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import Db, no_store, require_roles
from app.api.envelope import Envelope, Page, ok
from app.api.pagination import LIMIT, cut, decode_cursor
from app.core.security import temporary_password
from app.db.models.enums import UserRole
from app.repositories import event_logs, users
from app.schemas.user import AdminUserOut, RoleName, UserCreated, UserCreateIn
from app.services.accounts import create_user

router = APIRouter(prefix="/admin/users", tags=["계정 관리"], dependencies=[Depends(no_store)])
Admin = require_roles("ADMIN")


@router.get("", response_model=Envelope[Page[AdminUserOut]])
def list_users(
    request: Request,
    admin: Admin,
    db: Db,
    role: RoleName | None = None,
    admin_area_id: int | None = Query(None, gt=0),
    include_deleted: bool = False,
    cursor: str | None = None,
    limit: int = LIMIT,
):
    """USR-01. id 순."""
    after = decode_cursor(cursor, 1)
    rows = users.list_users(
        db,
        role=UserRole[role] if role else None,
        admin_area_id=admin_area_id,
        include_deleted=include_deleted,
        after_id=after[0] if after else None,
        limit=limit,
    )
    items, next_cursor, has_more = cut(rows, limit, lambda u: (u.id,))
    page = Page(
        items=[AdminUserOut.of(u) for u in items], next_cursor=next_cursor, has_more=has_more
    )
    return ok(request, page)


@router.post("", status_code=201, response_model=Envelope[UserCreated])
def issue_user(body: UserCreateIn, request: Request, admin: Admin, db: Db):
    """USR-02. 임시 비밀번호는 이 응답에서만 보여 주고 로그 · DB에 원문을 남기지 않는다."""
    password = temporary_password()
    user = create_user(
        db,
        login_id=body.login_id,
        name=body.name,
        organization=body.organization,
        role=UserRole[body.role],
        admin_area_id=body.admin_area_id,
        password=password,
    )
    details = {"target_user_id": user.id, "login_id": user.login_id, "role": body.role}
    event_logs.add(db, "USER_CREATED", user_id=admin.id, details=details)
    db.commit()
    db.refresh(user)
    return ok(request, UserCreated(user=AdminUserOut.of(user), temporary_password=password))
