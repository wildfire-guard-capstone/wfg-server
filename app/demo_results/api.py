"""(김지오 전용 · 임시) 이 API의 라우터 묶음 — app/main.py가 include한다."""

from fastapi import APIRouter

from app.demo_results import auth_router, incidents_router

router = APIRouter()
router.include_router(auth_router.router)
router.include_router(incidents_router.router)
