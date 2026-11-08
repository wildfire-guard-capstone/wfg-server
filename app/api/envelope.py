"""성공 응답 `{data, meta}`와 목록 `Page`(명세 2.2), 요청 id."""

import uuid
from typing import Any

from fastapi import FastAPI, Request
from pydantic import BaseModel


class Meta(BaseModel):
    request_id: str


class Envelope[T](BaseModel):
    data: T
    meta: Meta


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None
    has_more: bool


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def ok(request: Request, data: Any) -> dict[str, Any]:
    """`{data, meta}`. 라우터는 response_model=Envelope[...]과 함께 쓴다."""
    return {"data": data, "meta": {"request_id": request_id(request)}}


def add_request_id(app: FastAPI) -> None:
    """요청마다 id를 붙여 응답 meta와 `X-Request-ID` 헤더로 돌려준다(오류 추적용)."""

    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        rid = f"req-{uuid.uuid4().hex[:16]}"
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
