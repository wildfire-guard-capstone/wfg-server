"""비밀번호 해시와 로그인 토큰. 표준 라이브러리만 쓴다(새 의존성 없음).

- 비밀번호: PBKDF2-HMAC-SHA256, ``pbkdf2_sha256$<반복>$<salt>$<hash>`` (base64url).
- 토큰: ``<payload>.<서명>`` — payload = base64url(JSON {sub, role, iat, exp}),
  서명 = HMAC-SHA256(auth_secret). JWT HS256과 같은 원리를 최소한으로 구현했다.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

PBKDF2_ITERATIONS = 600_000
MIN_SECRET_LENGTH = 32


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _b64d(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, _b64d(digest))


@dataclass(frozen=True)
class TokenClaims:
    sub: str
    role: str
    iat: int
    exp: int


class InvalidToken(Exception):
    pass


def _sign(body: str, secret: str) -> str:
    return _b64e(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())


def issue_token(
    sub: str, role: str, secret: str, ttl_seconds: int, *, now: int | None = None
) -> str:
    iat = int(time.time()) if now is None else now
    payload = {"sub": sub, "role": role, "iat": iat, "exp": iat + ttl_seconds}
    body = _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"{body}.{_sign(body, secret)}"


def read_token(token: str, secret: str, *, now: int | None = None) -> TokenClaims:
    try:
        body, sig = token.split(".")
    except ValueError as e:
        raise InvalidToken("형식 오류") from e
    if not hmac.compare_digest(sig, _sign(body, secret)):
        raise InvalidToken("서명 불일치")
    try:
        claims = TokenClaims(**json.loads(_b64d(body)))
    except (ValueError, TypeError) as e:
        raise InvalidToken("내용 오류") from e
    if claims.exp <= (int(time.time()) if now is None else now):
        raise InvalidToken("만료")
    return claims
