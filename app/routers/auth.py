from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import issue_token
from app.deps import CurrentUser, SettingsDep, account_store, auth_secret
from app.schemas.auth import LoginRequest, LoginResponse, UserOut
from app.services.accounts import Account, AccountLocked, AccountStore

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user(a: Account) -> UserOut:
    return UserOut(login_id=a.login_id, name=a.name, organization=a.organization, role=a.role)


@router.post("/login")
def login(
    body: LoginRequest,
    settings: SettingsDep,
    secret: Annotated[str, Depends(auth_secret)],
    accounts: Annotated[AccountStore, Depends(account_store)],
) -> LoginResponse:
    try:
        account = accounts.authenticate(body.login_id, body.password)
    except AccountLocked as e:
        raise HTTPException(
            status.HTTP_423_LOCKED,
            "로그인 실패가 5회를 넘어 잠긴 계정입니다. 관리자에게 문의하세요",
        ) from e
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "아이디 또는 비밀번호가 맞지 않습니다")
    ttl = settings.token_ttl_minutes * 60
    now = datetime.now(UTC)
    return LoginResponse(
        access_token=issue_token(account.login_id, account.role.name, secret, ttl),
        expires_at=now + timedelta(seconds=ttl),
        user=_user(account),
    )


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return _user(user)
