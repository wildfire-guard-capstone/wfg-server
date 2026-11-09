"""기상 — 기상청에서 받은 시간별 기상(관측 · 예보, Obsidian 05 기상 설계 11/9).

관측 · 예보를 같은 모양으로 저장한다. 같은 위치 · 같은 자료 · 같은 발표분은 한 번만 받는다.
쓰는 범위만 저장: 예보는 예측이 쓸 수 있는 시간까지(초단기 6시간 · 단기 발표 후 12시간),
값은 풍향 · 풍속 · 기온 · 습도 · 강수 다섯 가지. 시험값(fixed · file)은 넣지 않는다.
"""

from datetime import datetime

from sqlalchemy import REAL, BigInteger, CheckConstraint, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._types import created_at

# KMA_NCST 초단기실황(관측) · KMA_ULTRA 초단기예보 · KMA_VILLAGE 단기예보 · ASOS 지상관측(관측)
WEATHER_SOURCES = ("KMA_NCST", "KMA_ULTRA", "KMA_VILLAGE", "ASOS")
OBSERVED = ("KMA_NCST", "ASOS")


class WeatherHour(Base):
    """한 위치 · 한 시각의 기상 1개(관측이거나 어느 발표분의 예보)."""

    __tablename__ = "weather_hours"
    __table_args__ = (
        UniqueConstraint("location", "source", "issued_at", "at"),
        CheckConstraint(f"source IN {WEATHER_SOURCES}", name="weather_source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 「grid:88,102」(기상청 5 km 격자) · 「stn:278」(지상관측 지점)
    location: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(12))
    # 발표(기준) 시각. 관측은 관측 정시와 같다
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 이 값의 시각(정시)
    wind_dir: Mapped[float | None] = mapped_column(REAL)  # ° 불어오는 쪽
    wind_speed: Mapped[float | None] = mapped_column(REAL)  # m/s
    temp: Mapped[float | None] = mapped_column(REAL)  # ℃
    humidity: Mapped[float | None] = mapped_column(REAL)  # %
    precip: Mapped[float | None] = mapped_column(REAL)  # 1시간 강수 mm(예보 글은 mm로 바꿈)
    precip_text: Mapped[str | None] = mapped_column(String(20))  # 예보 강수 원문(「1mm 미만」)
    fetched_at: Mapped[datetime] = created_at()
