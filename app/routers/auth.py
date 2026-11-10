"""AUTH-01 로그인 · AUTH-02 내 계정 · AUTH-03 로그아웃 기록."""

from fastapi import APIRouter, Request, Response

from app.api.deps import AnyUser, Db
from app.api.envelope import Envelope, ok
from app.api.errors import ApiError
from app.core.security import create_access_token, verify_password
from app.repositories import event_logs, users
from app.schemas.user import AuthToken, LoginIn, UserOut

router = APIRouter(prefix="/auth", tags=["인증"])

BAD_LOGIN = "아이디 또는 비밀번호가 올바르지 않습니다."


@router.post("/login", response_model=Envelope[AuthToken])
def login(body: LoginIn, request: Request, db: Db):
    """AUTH-01. 잠금 · 회수를 먼저 보고, 비밀번호 · 역할이 틀리면 실패 횟수 +1(5회면 잠금)."""
    user = users.by_login_id(db, body.login_id)
    if user is None:
        raise ApiError(401, "INVALID_CREDENTIALS", BAD_LOGIN)
    if users.is_revoked(user):
        raise ApiError(403, "ACCOUNT_UNAVAILABLE", "회수된 계정입니다. 전산 관리자에게 문의하세요.")
    if users.is_locked(user):
        raise ApiError(403, "ACCOUNT_UNAVAILABLE", "계정이 잠겼습니다. 전산 관리자에게 문의하세요.")
    password_ok = verify_password(body.password, user.password_hash)
    if not password_ok or user.role.name != body.role:
        user.failed_login_count += 1
        reason = "password" if not password_ok else "role"
        details = {"reason": reason, "failed_count": user.failed_login_count}
        event_logs.add(db, "LOGIN_FAILED", user_id=user.id, details=details)
        db.commit()
        if not password_ok:
            raise ApiError(401, "INVALID_CREDENTIALS", BAD_LOGIN)
        raise ApiError(403, "ROLE_MISMATCH", "선택한 사용자 구분이 계정 권한과 다릅니다.")
    user.failed_login_count = 0
    event_logs.add(db, "LOGIN", user_id=user.id)
    db.commit()
    token, expires = create_access_token(user.id, user.role.name)
    return ok(request, AuthToken(access_token=token, expires_in=expires, user=UserOut.of(user)))


@router.get("/me", response_model=Envelope[UserOut])
def me(request: Request, user: AnyUser):
    """AUTH-02."""
    return ok(request, UserOut.of(user))


@router.post("/logout", status_code=204)
def logout(user: AnyUser, db: Db):
    """AUTH-03. 기록만 남긴다. 토큰은 프론트가 지운다(서버 즉시 폐기는 Q-09)."""
    event_logs.add(db, "LOGOUT", user_id=user.id)
    db.commit()
    return Response(status_code=204)
