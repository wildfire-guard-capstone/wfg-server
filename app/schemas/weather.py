"""기상 DTO: WTH-01 지금 날씨(05 기상 설계 4)."""

from pydantic import BaseModel

from app.schemas.common import DateTimeOut


class WeatherNowOut(BaseModel):
    observed_at: DateTimeOut  # 관측 정시(실시간이 아니면 최신 예측의 0시)
    source: str  # KMA_NCST(초단기실황) · ASOS · 예측 기상 출처(FIXED · SYNTHETIC …)
    wind_dir: float | None  # ° 불어오는 쪽
    wind_dir_name: str | None  # 16방위(「남동」)
    wind_speed: float | None  # m/s
    temp: float | None  # ℃
    humidity: float | None  # %
    precip: float | None  # 1시간 강수 mm
    next_refresh_at: DateTimeOut | None  # 이때 다시 부르면 새 관측. 실시간이 아니면 null
    stale: bool  # 기상청 실패로 지난 관측을 준 것
