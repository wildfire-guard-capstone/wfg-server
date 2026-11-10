"""USR-01 계정 목록 · USR-02 발급 · USR-03 변경 · USR-04 회수 · USR-05 비밀번호 재발급 ·
USR-06 잠금 해제(전산 관리자)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from app.api.deps import Db, no_store, require_roles
from app.api.envelope import Envelope, Page, ok
from app.api.errors import ApiError
from app.api.pagination import LIMIT, cut, decode_cursor
from app.core.security import temporary_password
from app.db.models.enums import UserRole
from app.repositories import event_logs, users
from app.schemas.user import (
    AdminUserOut,
    PasswordReset,
    RoleName,
    UserCreated,
    UserCreateIn,
    UserUpdateIn,
)
from app.services import accounts
from app.services.accounts import create_user

router = APIRouter(prefix="/admin/users", tags=["계정 관리"], dependencies=[Depends(no_store)])
Admin = require_roles("ADMIN")
UserId = Annotated[int, Path(gt=0)]


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


def _not_self(admin_id: int, user_id: int, what: str) -> None:
    """자기 계정의 역할을 바꾸거나 회수하면 관리 화면에 다시 들어올 수 없다."""
    if admin_id == user_id:
        raise ApiError(409, "SELF_MODIFICATION", f"자기 계정은 {what} 수 없습니다.")


@router.patch("/{user_id}", response_model=Envelope[AdminUserOut])
def update_user(user_id: UserId, body: UserUpdateIn, request: Request, admin: Admin, db: Db):
    """USR-03. 보낸 값만 바꾼다. 바뀐 권한은 그 계정의 다음 요청부터 적용된다(매 요청 DB 확인)."""
    user = accounts.active_user(db, user_id)
    changes = body.model_dump(exclude_unset=True)
    if "role" in changes:
        changes["role"] = UserRole[changes["role"]]
        if changes["role"] is not user.role:
            _not_self(admin.id, user.id, "역할을 바꿀")
    changed = accounts.update_user(db, user, changes)
    if changed:
        details = {"target_user_id": user.id, "changed": changed, "role": user.role.name}
        event_logs.add(db, "USER_UPDATED", user_id=admin.id, details=details)
    db.commit()
    db.refresh(user)
    return ok(request, AdminUserOut.of(user))


@router.delete("/{user_id}", status_code=204)
def revoke_user(user_id: UserId, admin: Admin, db: Db):
    """USR-04. 행은 남기고 deleted_at만 기록한다. 이미 회수한 계정이면 그대로 204."""
    user = accounts.get_user(db, user_id)
    _not_self(admin.id, user.id, "회수할")
    if accounts.revoke_user(db, user):
        details = {"target_user_id": user.id, "login_id": user.login_id, "role": user.role.name}
        event_logs.add(db, "USER_REVOKED", user_id=admin.id, details=details)
    db.commit()
    return Response(status_code=204)


@router.post("/{user_id}/password-reset", response_model=Envelope[PasswordReset])
def reset_password(user_id: UserId, request: Request, admin: Admin, db: Db):
    """USR-05. 새 임시 비밀번호는 이 응답에서만 보여 준다(로그 · DB에 원문 없음).
    이미 발급된 토큰은 만료 때까지 쓸 수 있다(즉시 폐기는 명세 Q-09)."""
    user = accounts.active_user(db, user_id)
    password = temporary_password()
    accounts.reset_password(db, user, password)
    event_logs.add(db, "USER_PASSWORD_RESET", user_id=admin.id, details={"target_user_id": user.id})
    db.commit()
    return ok(request, PasswordReset(user_id=user.id, temporary_password=password))


@router.post("/{user_id}/unlock", response_model=Envelope[AdminUserOut])
def unlock_user(user_id: UserId, request: Request, admin: Admin, db: Db):
    """USR-06. 로그인 실패 횟수를 0으로. 회수한 계정은 다시 살리지 않는다(409)."""
    user = accounts.active_user(db, user_id)
    was = user.failed_login_count
    accounts.unlock_user(db, user)
    details = {"target_user_id": user.id, "failed_count": was}
    event_logs.add(db, "USER_UNLOCKED", user_id=admin.id, details=details)
    db.commit()
    db.refresh(user)
    return ok(request, AdminUserOut.of(user))
