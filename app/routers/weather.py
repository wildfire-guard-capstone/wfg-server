"""WTH-01 지금 날씨(05 기상 설계 4). 산불 상세와 따로 둔다 — 기상청이 느리거나 죽어도
산불 상세는 영향받지 않게."""

import logging
from datetime import datetime

from fastapi import APIRouter, Request

from app.api.deps import Db, require_roles
from app.api.envelope import Envelope, ok
from app.api.errors import ApiError
from app.routers.incidents import IncidentId, load_incident
from app.schemas.weather import WeatherNowOut
from app.services.weather import observer_from_settings, weather_now
from app.weather import WeatherUnavailable
from app.weather.base import KST

log = logging.getLogger(__name__)
router = APIRouter(prefix="/incidents/{incident_id}", tags=["기상"])
Analyst = require_roles("COMMANDER", "VIEWER")


@router.get("/weather/now", response_model=Envelope[WeatherNowOut])
def get_weather_now(incident_id: IncidentId, request: Request, user: Analyst, db: Db):
    """WTH-01. 같은 격자 · 같은 정시는 기상청을 한 번만 부른다(처음 물을 때 저장).
    프론트는 화면을 열 때 1번, `next_refresh_at`이 지나면 다시 부르면 된다."""
    incident = load_incident(db, user, incident_id)
    try:
        out = weather_now(db, incident, observer=observer_from_settings(), now=datetime.now(KST))
    except WeatherUnavailable as e:
        log.warning("지금 날씨 없음 — 산불 %s: %s", incident_id, e)
        raise ApiError(503, "WEATHER_UNAVAILABLE", "기상 자료를 받을 수 없습니다.") from None
    db.commit()
    return ok(request, WeatherNowOut(**out))
