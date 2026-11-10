"""기상 제공자 — 갈아 끼우는 자리(app.predict · app.propose와 같은 방식).

새 기상 원천은 이 폴더에 파일 하나를 만들고(base.WeatherProvider 약속을 지킴)
아래 WEATHERS에 이름을 더한다.
지금: fixed(고정 시험값) · file(기상 JSON) · kma(기상청 단기예보, 운영) · asos(지상관측, 과거 시연).

예측 흐름: 예측 요청 때 설정 WEATHER로 고른 원천에서 9시간을 받아(못 받으면 요청이 503
WEATHER_UNAVAILABLE) 예측에 넘기고 `predictions.weather`로 저장한다(routers.predictions).
"""

from pathlib import Path
from typing import Any

from app.weather.asos import AsosObserved
from app.weather.base import Weather, WeatherProvider, WeatherRequest, WeatherUnavailable
from app.weather.file import FileWeather
from app.weather.fixed import FixedWeather
from app.weather.kma import KmaForecast

WEATHERS = {
    "fixed": FixedWeather,
    "file": FileWeather,
    "kma": KmaForecast,
    "asos": AsosObserved,
}
# 이 원천은 이 설정이 꼭 있어야 한다(없으면 서버를 켤 때가 아니라 고를 때 알린다)
REQUIRED = {"file": "path", "kma": "service_key", "asos": "auth_key"}


def get_weather(name: str, **options: Any) -> WeatherProvider:
    """이름으로 기상 제공자를 만든다.
    options는 그 원천의 설정(file이면 path, kma면 service_key, asos면 auth_key · station)."""
    if name not in WEATHERS:
        raise ValueError(f"모르는 기상: {name} (가능: {', '.join(WEATHERS)})")
    need = REQUIRED.get(name)
    if need and not options.get(need):
        raise ValueError(f"기상 {name}에는 {need} 설정이 필요합니다")
    if name == "file":
        options["path"] = Path(options["path"])
    return WEATHERS[name](**options)


__all__ = [
    "WEATHERS",
    "Weather",
    "WeatherProvider",
    "WeatherRequest",
    "WeatherUnavailable",
    "get_weather",
]
