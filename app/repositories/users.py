"""계정 읽기 · 쓰기(DB 설계 2.1)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import User

LOCK_AFTER = 5  # 연속 실패 5회면 잠금(UC-AUTH-01 E3)


def by_login_id(session: Session, login_id: str) -> User | None:
    """대소문자 무시."""
    return session.scalar(select(User).where(func.lower(User.login_id) == login_id.lower()))


def is_locked(user: User) -> bool:
    return user.failed_login_count >= LOCK_AFTER


def is_revoked(user: User) -> bool:
    return user.deleted_at is not None
