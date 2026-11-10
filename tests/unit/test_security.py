from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    temporary_password,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("pw-1234")
    assert h.startswith("$argon2")
    assert verify_password("pw-1234", h)
    assert not verify_password("wrong", h)


def test_non_hash_value_never_logs_in():
    assert not verify_password("anything", "!")


def test_temporary_password_is_random():
    assert len(temporary_password()) == 12
    assert temporary_password() != temporary_password()


def test_token_roundtrip_and_expiry():
    token, expires = create_access_token(7, "COMMANDER")
    assert expires == 1800
    claims = decode_access_token(token)
    assert claims["sub"] == "7" and claims["role"] == "COMMANDER"
    old, _ = create_access_token(7, "COMMANDER", now=datetime.now(UTC) - timedelta(hours=2))
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(old)


def test_tampered_token_is_rejected():
    token, _ = create_access_token(7, "COMMANDER")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token[:-2] + ("AA" if not token.endswith("AA") else "BB"))
