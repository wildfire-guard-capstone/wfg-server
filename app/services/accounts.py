"""계정 발급 · 변경 · 회수: 관리 화면(USR-02 ~ 06)과 명령줄(첫 전산 관리자)이 같이 쓴다."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import ApiError, not_found
from app.core.security import hash_password
from app.db.models import User
from app.db.models.enums import UserRole
from app.repositories import users


def check_jurisdiction(session: Session, role: UserRole, admin_area_id: int | None) -> None:
    """관할: 전산 관리자만 없음(null), 나머지는 시군구여야 한다(명세 USR-02)."""
    if role is UserRole.ADMIN:
        if admin_area_id is not None:
            raise ApiError(422, "INVALID_JURISDICTION", "전산 관리자는 관할을 두지 않습니다.")
        return
    level = session.scalar(
        text("SELECT level::text FROM admin_areas WHERE id = :id"), {"id": admin_area_id}
    )
    if level != "SIGUNGU":
        raise ApiError(422, "INVALID_JURISDICTION", "관할은 시군구를 골라야 합니다.")


def create_user(
    session: Session,
    *,
    login_id: str,
    name: str,
    organization: str,
    role: UserRole,
    admin_area_id: int | None,
    password: str,
) -> User:
    """중복 아이디(대소문자 무시)면 409. 커밋은 부르는 쪽이 한다."""
    if users.by_login_id(session, login_id) is not None:
        raise ApiError(409, "DUPLICATE_LOGIN_ID", "이미 있는 아이디입니다.")
    check_jurisdiction(session, role, admin_area_id)
    user = User(
        login_id=login_id,
        password_hash=hash_password(password),
        name=name,
        organization=organization,
        role=role,
        admin_area_id=admin_area_id,
    )
    session.add(user)
    session.flush()
    return user


def sigungu_id(session: Session, name: str) -> int:
    """시군구 이름 → id(명령줄용). 같은 이름이 여럿이면 오류."""
    ids = session.scalars(
        text("SELECT id FROM admin_areas WHERE level = 'SIGUNGU' AND name = :n"), {"n": name}
    ).all()
    if len(ids) != 1:
        raise ValueError(f"시군구 「{name}」이 {len(ids)}곳입니다(1곳이어야 함)")
    return ids[0]


def get_user(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise not_found("계정을 찾을 수 없습니다.")
    return user


def active_user(session: Session, user_id: int) -> User:
    """회수한 계정은 바꾸지 않는다(다시 살리지 않음, 명세 USR-06)."""
    user = get_user(session, user_id)
    if users.is_revoked(user):
        raise ApiError(409, "ACCOUNT_REVOKED", "회수된 계정입니다.")
    return user


def _keep_a_commander(session: Session, user: User) -> None:
    """관할의 마지막 통합지휘권자는 회수하거나 그 역할 · 관할에서 뺄 수 없다(명세 USR-03 · 04)."""
    if user.role is not UserRole.COMMANDER or user.admin_area_id is None:
        return
    if users.other_commanders(session, user.admin_area_id, user.id) == 0:
        raise ApiError(
            409,
            "LAST_COMMANDER",
            "이 관할의 마지막 통합지휘권자입니다. 먼저 다른 계정을 지정하세요.",
        )


def update_user(session: Session, user: User, changes: dict[str, Any]) -> list[str]:
    """보낸 값만 바꾸고 실제로 바뀐 필드 이름을 돌려준다. 커밋은 부르는 쪽이 한다.

    changes의 role은 UserRole. 역할 · 관할은 바꾼 뒤의 값으로 다시 검사한다."""
    role = changes.get("role", user.role)
    area = changes["admin_area_id"] if "admin_area_id" in changes else user.admin_area_id
    if (role, area) != (user.role, user.admin_area_id):
        check_jurisdiction(session, role, area)
        _keep_a_commander(session, user)
    changed = [k for k, v in changes.items() if getattr(user, k) != v]
    for k in changed:
        setattr(user, k, changes[k])
    session.flush()
    return changed


def revoke_user(session: Session, user: User) -> bool:
    """회수: 행은 남기고 deleted_at만 기록한다. 이미 회수했으면 그대로 두고 False."""
    if users.is_revoked(user):
        return False
    _keep_a_commander(session, user)
    user.deleted_at = datetime.now(UTC)
    session.flush()
    return True


def reset_password(session: Session, user: User, password: str) -> None:
    """새 비밀번호의 해시만 저장한다. 잠금(실패 횟수)은 그대로다 — 푸는 것은 USR-06."""
    user.password_hash = hash_password(password)
    session.flush()


def unlock_user(session: Session, user: User) -> None:
    user.failed_login_count = 0
    session.flush()
