"""오류 응답 `{error: {code, message, fields}, meta}`(명세 2.3)."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.envelope import request_id

log = logging.getLogger(__name__)

HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
}


class ApiError(Exception):
    """업무 오류. 라우터 · 서비스가 던지면 명세 형식으로 바뀐다."""

    def __init__(
        self, status: int, code: str, message: str, fields: list[dict[str, str]] | None = None
    ):
        super().__init__(message)
        self.status, self.code, self.message, self.fields = status, code, message, fields or []


def not_found(message: str = "대상을 찾을 수 없습니다.") -> ApiError:
    return ApiError(404, "NOT_FOUND", message)


def error_response(
    request: Request, status: int, code: str, message: str, fields: list[Any] | None = None
) -> JSONResponse:
    body = {
        "error": {"code": code, "message": message, "fields": fields or []},
        "meta": {"request_id": request_id(request)},
    }
    return JSONResponse(status_code=status, content=body)


def _field(loc: tuple[Any, ...]) -> str:
    """("body", "initial_perimeter", "geometry") → "initial_perimeter.geometry"."""
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts) or ".".join(str(p) for p in loc)


def install_error_handlers(app: FastAPI, prefix: str = "/api/v1") -> None:
    """이 형식은 `prefix` 아래 경로(주 API)에만 쓴다.

    그 밖의 경로(임시 결과 조회 API 등)는 FastAPI 기본 형식 그대로 둔다.
    """

    def ours(request: Request) -> bool:
        return request.url.path.startswith(prefix)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(request, exc.status, exc.code, exc.message, exc.fields)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        if not ours(request):
            return await request_validation_exception_handler(request, exc)
        errors = exc.errors()
        if any(e.get("type") == "json_invalid" for e in errors):
            return error_response(
                request, 400, "BAD_REQUEST", "요청 본문이 올바른 JSON이 아닙니다."
            )
        fields = [
            {"field": _field(tuple(e.get("loc", ()))), "reason": e.get("msg", "")} for e in errors
        ]
        return error_response(request, 422, "VALIDATION_ERROR", "입력값을 확인해주세요.", fields)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        if not ours(request):
            return await http_exception_handler(request, exc)
        code = HTTP_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
        message = exc.detail if isinstance(exc.detail, str) else code
        return error_response(request, exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("처리 중 예상하지 못한 오류 (%s)", request_id(request))
        if not ours(request):
            return PlainTextResponse("Internal Server Error", status_code=500)
        return error_response(request, 500, "INTERNAL_ERROR", "처리 중 오류가 발생했습니다.")
