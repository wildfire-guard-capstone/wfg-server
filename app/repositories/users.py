"""계정 읽기 · 쓰기(DB 설계 2.1)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.models.enums import UserRole

LOCK_AFTER = 5  # 연속 실패 5회면 잠금(UC-AUTH-01 E3)


def by_login_id(session: Session, login_id: str) -> User | None:
    """대소문자 무시."""
    return session.scalar(select(User).where(func.lower(User.login_id) == login_id.lower()))


def is_locked(user: User) -> bool:
    return user.failed_login_count >= LOCK_AFTER


def is_revoked(user: User) -> bool:
    return user.deleted_at is not None


def list_users(
    session: Session,
    *,
    role: UserRole | None,
    admin_area_id: int | None,
    include_deleted: bool,
    after_id: int | None,
    limit: int,
) -> list[User]:
    """id 오름차순, limit + 1개(다음 쪽이 있는지 보려고)."""
    q = select(User).order_by(User.id).limit(limit + 1)
    if role is not None:
        q = q.where(User.role == role)
    if admin_area_id is not None:
        q = q.where(User.admin_area_id == admin_area_id)
    if not include_deleted:
        q = q.where(User.deleted_at.is_(None))
    if after_id is not None:
        q = q.where(User.id > after_id)
    return list(session.scalars(q))
