"""계정 발급: 관리 화면(USR-02)과 명령줄(첫 전산 관리자)이 같이 쓴다."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import ApiError
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
