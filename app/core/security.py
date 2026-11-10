"""비밀번호 해시(argon2)와 접근 토큰(JWT HS256)."""

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from app.core.config import get_settings

log = logging.getLogger(__name__)

ALGORITHM = "HS256"
_hasher = PasswordHash.recommended()
_fallback_secret = secrets.token_urlsafe(32)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """해시가 아닌 값(「!」 = 로그인 불가 계정)이면 거짓."""
    try:
        return _hasher.verify(password, password_hash)
    except UnknownHashError:
        return False


def temporary_password() -> str:
    """계정 발급 · 재발급 때 주는 임시 비밀번호(12자)."""
    return secrets.token_urlsafe(9)


_warned = False


def _secret() -> str:
    global _warned
    secret = get_settings().jwt_secret
    if secret:
        return secret
    if not _warned:
        log.warning("JWT_SECRET이 없어 임시 키를 씁니다(서버를 다시 켜면 다시 로그인).")
        _warned = True
    return _fallback_secret


def purpose_key(purpose: str) -> str:
    """로그인 토큰과 다른 용도(예: 정정 확인안)의 서명 키. 같은 비밀에서 갈라 내므로
    다른 용도의 토큰을 로그인 토큰으로 쓸 수 없다(서명이 맞지 않음)."""
    return hmac.new(_secret().encode(), purpose.encode(), hashlib.sha256).hexdigest()


def create_access_token(user_id: int, role: str, now: datetime | None = None) -> tuple[str, int]:
    """(토큰, 유효 초). 역할은 참고로만 넣고, 요청마다 DB의 역할을 다시 읽는다(명세 2.1)."""
    expires = get_settings().jwt_expires_s
    now = now or datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=expires),
    }
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM), expires


def decode_access_token(token: str) -> dict[str, Any]:
    """만료 · 위조면 jwt.ExpiredSignatureError · jwt.InvalidTokenError."""
    return jwt.decode(token, _secret(), algorithms=[ALGORITHM])
