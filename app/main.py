from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.core.config import get_settings
from app.routers import auth, health, incidents

app = FastAPI(title="Wildfire Guard API", version=__version__)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(incidents.router)

_origins = get_settings().cors_origins
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
