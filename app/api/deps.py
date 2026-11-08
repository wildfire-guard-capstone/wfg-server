"""라우터 공통 의존성: DB 세션, 로그인 사용자, 역할 검사(명세 2.1 · 3.1)."""

from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.security import decode_access_token
from app.db.models import User
from app.db.models.enums import UserRole
from app.db.session import get_session
from app.repositories import users

Db = Annotated[Session, Depends(get_session)]
_bearer = HTTPBearer(auto_error=False)


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)], db: Db
) -> User:
    """토큰을 확인하고 매 요청 DB에서 계정을 다시 읽는다(역할 · 회수 · 잠금은 DB 값)."""
    if credentials is None:
        raise ApiError(401, "UNAUTHORIZED", "로그인이 필요합니다.")
    try:
        claims = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(401, "TOKEN_EXPIRED", "로그인이 만료되었습니다. 다시 로그인하세요.") from exc
    except jwt.InvalidTokenError as exc:
        raise ApiError(401, "INVALID_TOKEN", "로그인 정보가 올바르지 않습니다.") from exc
    user = db.get(User, int(claims["sub"]))
    if user is None:
        raise ApiError(401, "INVALID_TOKEN", "로그인 정보가 올바르지 않습니다.")
    if users.is_revoked(user) or users.is_locked(user):
        raise ApiError(
            403, "ACCOUNT_UNAVAILABLE", "사용할 수 없는 계정입니다. 전산 관리자에게 문의하세요."
        )
    return user


def require_roles(*roles: str):
    """허용 역할(COMMANDER · VIEWER · REPORTER · ADMIN)만 통과하는 의존성 타입."""
    allowed = {UserRole[r] for r in roles}

    def _check(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role not in allowed:
            raise ApiError(403, "FORBIDDEN_ROLE", "이 기능을 쓸 권한이 없습니다.")
        return user

    return Annotated[User, Depends(_check)]


AnyUser = require_roles("COMMANDER", "VIEWER", "REPORTER", "ADMIN")
