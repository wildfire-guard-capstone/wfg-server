"""Input model for one spread prediction run.

Matches the spec's prediction flow: start from the latest observed perimeter,
or from the ignition point when no perimeter has been reported yet.
"""

from datetime import datetime

from pydantic import BaseModel, Field

LonLat = tuple[float, float]


class Ignition(BaseModel):
    lon: float
    lat: float
    time: datetime


class WeatherObs(BaseModel):
    """One hourly weather record (SI units, meteorological wind direction = blowing from)."""

    time: datetime
    wind_speed_ms: float = Field(ge=0)
    wind_dir_deg: float = Field(ge=0, le=360)
    temperature_c: float | None = None
    humidity_pct: float | None = None


class FireCase(BaseModel):
    case_id: str
    ignition: Ignition
    t0: datetime
    start_perimeter: list[LonLat] | None = None
    weather: list[WeatherObs]

    @property
    def starts_from_perimeter(self) -> bool:
        return self.start_perimeter is not None
