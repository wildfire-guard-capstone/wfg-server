"""고정 시험값: 시간마다 같은 기상. 기상청 없이 흐름을 시험할 때 쓴다.

기본값은 약한 바람의 평범한 날(11/9): 서풍 2 m/s(바람 기준 초기 대응), 기온 15 ℃, 습도 40 %, 강수 0.
"""

from dataclasses import dataclass

from app.weather.base import Weather, WeatherRequest, hour, hour_slots


@dataclass(frozen=True)
class FixedWeather:
    name: str = "fixed"
    wind_ms: float = 2.0
    wind_deg: float = 270.0  # 불어오는 방향(서풍 = 270)
    temp: float = 15.0
    humidity: float = 40.0
    precip: float = 0.0

    def fetch(self, request: WeatherRequest) -> Weather:
        hours = [
            hour(t, "고정", self.wind_deg, self.wind_ms, self.temp, self.humidity, self.precip)
            for t in hour_slots(request.base_at)
        ]
        return {"source": "FIXED", "issued_at": None, "location": None, "hours": hours}
