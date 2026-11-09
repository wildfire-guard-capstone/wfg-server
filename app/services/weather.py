"""WTH-01 지금 날씨: 테이블 먼저, 없으면 기상청 초단기실황 1번 → 저장(05 기상 설계 3 · 4).

같은 격자 · 같은 정시는 한 번만 부른다 — 화면이 몇 번을 물어도 기상청은 1시간에 1번.
실시간 원천(설정 WEATHER=kma)이 아니면 기상청을 부르지 않고 최신 예측의 0시 값을 준다
(과거 시연 · 시험에서 「2025년 산불」에 오늘 날씨가 뜨지 않게).
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from geoalchemy2.shape import to_shape
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Incident, WeatherHour
from app.db.models.enums import IncidentStatus
from app.repositories import incidents, predictions, weather
from app.repositories.engine_inputs import site_of
from app.weather import WeatherUnavailable, get_weather
from app.weather.kma import KmaForecast, latest_observed_hour
from rules.core.direction import compass16

log = logging.getLogger(__name__)
REFRESH_DELAY = timedelta(hours=1, minutes=40)  # 다음 정시 자료가 나오는 때 = 정시 + 1시간 40분


def observer_from_settings() -> KmaForecast | None:
    """지금 날씨를 실시간으로 받는 원천. 설정 WEATHER=kma일 때만(키 DATA_GO_KR_API_KEY)."""
    s = get_settings()
    if s.weather != "kma":
        return None
    return get_weather("kma", service_key=s.data_go_kr_api_key)


def _now_out(
    *,
    observed_at: datetime,
    source: str,
    values: dict[str, Any],
    next_refresh_at: datetime | None,
    stale: bool,
) -> dict[str, Any]:
    wind_dir = values.get("wind_dir")
    return {
        "observed_at": observed_at,
        "source": source,
        "wind_dir": wind_dir,
        "wind_dir_name": None if wind_dir is None else compass16(wind_dir),
        "wind_speed": values.get("wind_speed"),
        "temp": values.get("temp"),
        "humidity": values.get("humidity"),
        "precip": values.get("precip"),
        "next_refresh_at": next_refresh_at,
        "stale": stale,
    }


def _row_out(row: WeatherHour, *, stale: bool) -> dict[str, Any]:
    keys = ("wind_dir", "wind_speed", "temp", "humidity", "precip")
    return _now_out(
        observed_at=row.at,
        source=row.source,
        values={k: getattr(row, k) for k in keys},
        next_refresh_at=None if stale else row.at + REFRESH_DELAY,
        stale=stale,
    )


def _from_prediction(session: Session, incident: Incident) -> dict[str, Any]:
    """최신 예측에 쓴 기상의 0시 값(기상청을 부르지 않음)."""
    latest = incidents.latest_prediction(session, incident.id)
    if latest is None:
        raise WeatherUnavailable("예측 전이라 보여 줄 기상이 없습니다")
    w = predictions.get_prediction(session, latest[0]).weather
    first = (w.get("hours") or [{}])[0]
    return _now_out(
        observed_at=datetime.fromisoformat(first["at"]),
        source=w.get("source", ""),
        values=first,
        next_refresh_at=None,
        stale=False,
    )


def weather_now(
    session: Session, incident: Incident, *, observer: KmaForecast | None, now: datetime
) -> dict[str, Any]:
    """지금 날씨 1개. 저장은 하고 커밋은 부르는 쪽이 한다.

    1. 실시간 원천이 없거나(WEATHER ≠ kma) 산불이 종료됐으면 → 최신 예측의 0시 값
    2. 최신 정시(지금 − 40분 내림) 관측이 테이블에 있으면 그대로
    3. 없으면 초단기실황 1번 → 저장 → 돌려줌
    4. 기상청 실패 → 테이블의 마지막 관측 + stale. 그것도 없으면 WeatherUnavailable
    """
    if observer is None or incident.status is IncidentStatus.ENDED:  # 1
        return _from_prediction(session, incident)
    site = site_of(to_shape(incident.ignition_point))
    location = weather.grid_location(site)
    at = latest_observed_hour(now)
    row = weather.observation(session, location, at)  # 2
    if row is not None:
        return _row_out(row, stale=False)
    try:  # 3
        values = observer.observation(site, at)
    except WeatherUnavailable:  # 4
        log.warning("지금 날씨: 기상청 실패 — %s %s", location, at.isoformat(), exc_info=True)
        last = weather.latest_observation(session, location)
        if last is None:
            raise
        return _row_out(last, stale=True)
    weather.save_hours(
        session, [{"location": location, "source": "KMA_NCST", "issued_at": at, "at": at, **values}]
    )
    return _now_out(
        observed_at=at,
        source="KMA_NCST",
        values=values,
        next_refresh_at=at + REFRESH_DELAY,
        stale=False,
    )
