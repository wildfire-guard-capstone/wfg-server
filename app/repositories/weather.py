"""기상 저장 · 읽기 — weather_hours(05 기상 설계). 같은 발표분은 처음 받은 값 그대로 둔다."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import WeatherHour
from app.db.models.weather import OBSERVED
from app.weather.kma import grid


def grid_location(site: tuple[float, float]) -> str:
    """기준점(위도, 경도) → 「grid:88,102」(기상청 5 km 격자).
    같은 격자의 산불은 한 행을 같이 쓴다."""
    nx, ny = grid(*site)
    return f"grid:{nx},{ny}"


def save_hours(session: Session, rows: list[dict[str, Any]]) -> None:
    """여러 시간을 넣는다. 이미 있는 (위치 · 자료 · 발표 · 시각)은 건너뛴다(겹쳐 쓰지 않음)."""
    if rows:
        stmt = (
            insert(WeatherHour)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["location", "source", "issued_at", "at"])
        )
        session.execute(stmt)


def observation(session: Session, location: str, at: datetime) -> WeatherHour | None:
    """그 위치 · 그 정시의 관측(실황 · 지상관측)."""
    return session.scalar(
        select(WeatherHour)
        .where(
            WeatherHour.location == location,
            WeatherHour.source.in_(OBSERVED),
            WeatherHour.at == at,
        )
        .limit(1)
    )


def latest_observation(session: Session, location: str) -> WeatherHour | None:
    """그 위치에 저장된 가장 최근 관측."""
    return session.scalar(
        select(WeatherHour)
        .where(WeatherHour.location == location, WeatherHour.source.in_(OBSERVED))
        .order_by(WeatherHour.at.desc())
        .limit(1)
    )
