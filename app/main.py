from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.envelope import add_request_id
from app.api.errors import install_error_handlers
from app.core.config import get_settings
from app.demo_results.api import router as demo_results_router  # 김지오 전용 · 임시(지울 때 이 줄)
from app.routers import (
    assistant,
    auth,
    event_logs,
    health,
    incidents,
    maps,
    predictions,
    proposals,
    resources,
    users,
    weather,
)

API = "/api/v1"

app = FastAPI(title="Wildfire Guard API", version=__version__)
add_request_id(app)
install_error_handlers(app, prefix=API)
app.include_router(health.router)
app.include_router(auth.router, prefix=API)
app.include_router(users.router, prefix=API)
app.include_router(incidents.router, prefix=API)
app.include_router(predictions.router, prefix=API)
app.include_router(proposals.router, prefix=API)
app.include_router(maps.router, prefix=API)
app.include_router(maps.areas_router, prefix=API)
app.include_router(resources.router, prefix=API)
app.include_router(event_logs.router, prefix=API)
app.include_router(weather.router, prefix=API)
app.include_router(assistant.router, prefix=API)

# 김지오 전용 · 임시 결과 조회 API — 지울 때 이 줄 · 위 import 줄과 app/demo_results를 지운다
app.include_router(demo_results_router)

_origins = get_settings().cors_origins
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )
