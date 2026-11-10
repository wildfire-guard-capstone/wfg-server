"""목록 커서(명세 2.1): 마지막 정렬 키를 담은 불투명 문자열. 같은 필터에서만 다시 쓴다."""

import base64
import json
from collections.abc import Callable, Sequence
from typing import Any

from fastapi import Query

from app.api.errors import ApiError

LIMIT = Query(20, ge=1, le=100, description="기본 20, 최대 100")
MAP_LIMIT = Query(500, ge=1, le=1000, description="기본 500, 최대 1000")


def encode_cursor(*keys: Any) -> str:
    raw = json.dumps(list(keys), separators=(",", ":"), default=str)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None, n: int) -> list[Any] | None:
    """커서 → 정렬 키 n개. 없으면 None, 깨졌으면 422."""
    if not cursor:
        return None
    try:
        keys = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(keys, list) or len(keys) != n:
            raise ValueError
        return keys
    except ValueError as exc:
        raise ApiError(
            422,
            "INVALID_CURSOR",
            "커서가 올바르지 않습니다.",
            [{"field": "cursor", "reason": "서버가 준 next_cursor를 그대로 보내세요."}],
        ) from exc


def cut[T](rows: Sequence[T], limit: int, key: Callable[[T], tuple[Any, ...]]):
    """limit + 1개를 읽어 온 행 → (이번 쪽, next_cursor, has_more)."""
    has_more = len(rows) > limit
    items = list(rows[:limit])
    next_cursor = encode_cursor(*key(items[-1])) if has_more and items else None
    return items, next_cursor, has_more
