"""(김지오 전용 · 임시) 라우터가 쓰는 의존성.

설정 · 계정 저장소 · 결과 저장소 · 로그인 확인 · 권한 확인.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db.models.enums import UserRole
from app.demo_results.accounts import Account, AccountStore
from app.demo_results.config import DemoSettings, get_demo_settings
from app.demo_results.results import BundleError, FileResultsStore
from app.demo_results.security import MIN_SECRET_LENGTH, InvalidToken, read_token

SettingsDep = Annotated[DemoSettings, Depends(get_demo_settings)]

# 경로마다 한 개 — 로그인 실패 횟수와 읽은 파일 캐시를 요청 사이에 유지한다.
_account_stores: dict[Path, AccountStore] = {}
_results_stores: dict[Path, FileResultsStore] = {}


def _unavailable(what: str) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"{what}이(가) 설정되지 않았습니다")


def auth_secret(settings: SettingsDep) -> str:
    secret = settings.auth_secret
    if not secret or len(secret) < MIN_SECRET_LENGTH:
        raise _unavailable(f"AUTH_SECRET({MIN_SECRET_LENGTH}자 이상)")
    return secret


def account_store(settings: SettingsDep) -> AccountStore:
    path = settings.accounts_file
    if path is None or not path.is_file():
        raise _unavailable("계정 파일(ACCOUNTS_FILE)")
    path = path.resolve()
    if path not in _account_stores:
        _account_stores[path] = AccountStore(path)
    return _account_stores[path]


def results_store(settings: SettingsDep) -> FileResultsStore:
    if settings.results_dir is None:
        raise _unavailable("결과 데이터(RESULTS_DIR)")
    path = settings.results_dir.resolve()
    if path not in _results_stores:
        try:
            _results_stores[path] = FileResultsStore(path)
        except BundleError as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    return _results_stores[path]


_bearer = HTTPBearer(auto_error=False)


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    secret: Annotated[str, Depends(auth_secret)],
    accounts: Annotated[AccountStore, Depends(account_store)],
) -> Account:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "로그인이 필요합니다",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        claims = read_token(credentials.credentials, secret)
    except InvalidToken as e:
        raise unauthorized from e
    # 역할은 토큰이 아니라 계정 파일에서 다시 읽는다 — 삭제 · 권한 변경이 바로 반영된다.
    account = accounts.get(claims.sub)
    if account is None:
        raise unauthorized
    return account


CurrentUser = Annotated[Account, Depends(current_user)]


def require_roles(*roles: UserRole) -> Callable[[Account], Account]:
    allowed = frozenset(roles)

    def check(user: CurrentUser) -> Account:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "이 정보를 볼 권한이 없습니다")
        return user

    return check
