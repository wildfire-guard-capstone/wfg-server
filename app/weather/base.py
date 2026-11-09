"""기상 제공자의 약속. 어떤 기상 원천이든 같은 요청을 받아 같은 모양의 기상을 돌려준다.

원천(고정 시험값 · 파일 · 기상청 단기예보 · 지상관측)은 이 약속만 지키면 갈아 끼울 수 있다.
서버가 기상을 받아 예측 제공자에게 넘기고 예측과 함께 저장한다(`predictions.weather`, 명세 Weather).
규칙 엔진 · 제안서는 그 저장값을 쓴다 — 예측에 쓴 기상과 제안서의 기상이 늘 같다.
기상 제공자는 DB를 모른다.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
HOURS = 8  # 0시 ~ 8시간 뒤 = 9개(예측 P1 ~ P8과 같은 범위)
HOUR_KEYS = ("at", "kind", "wind_dir", "wind_speed", "temp", "humidity", "precip")

# {"source", "issued_at", "location",
#  "hours": [{at, kind, wind_dir, wind_speed, temp, humidity, precip} × 9]}
# kind = 관측 · 예보 · 고정(0시는 관측, 뒤는 예보처럼 섞여 오므로 시간마다 표시)
Weather = dict[str, Any]


@dataclass(frozen=True)
class WeatherRequest:
    site: tuple[float, float]  # 기준점 위도 · 경도(WGS84) — 발화점
    base_at: datetime  # 0시 = 화선 관측 시각. 시간별 값은 이를 내림한 정시부터 9개


class WeatherUnavailable(Exception):
    """기상 원천에서 값을 받지 못했다(연결 실패 · 응답 오류 · 받을 수 없는 시각).
    예측 요청(PRED-01)은 작업을 시작하지 않고 503 WEATHER_UNAVAILABLE로 거절한다(명세 v0.4)."""


class WeatherProvider(Protocol):
    name: str

    def fetch(self, request: WeatherRequest) -> Weather: ...


def hour_slots(base_at: datetime) -> list[datetime]:
    """base_at을 내림한 정시부터 1시간 간격 9개(한국 시간)."""
    start = base_at.astimezone(KST).replace(minute=0, second=0, microsecond=0)
    return [start + timedelta(hours=h) for h in range(HOURS + 1)]


def hour(
    at: datetime,
    kind: str,
    wind_dir: float,
    wind_speed: float,
    temp: float,
    humidity: float,
    precip: float,
) -> dict[str, Any]:
    return {
        "at": at.isoformat(),
        "kind": kind,
        "wind_dir": float(wind_dir),
        "wind_speed": float(wind_speed),
        "temp": float(temp),
        "humidity": float(humidity),
        "precip": float(precip),
    }


def check_weather(weather: Weather) -> Weather:
    """모양 검사: 9시간, 시간마다 칸이 다 있고 풍향 0 ~ 360 · 풍속 · 강수 0 이상 · 습도 0 ~ 100."""
    hours = weather.get("hours") or []
    if len(hours) != HOURS + 1:
        raise ValueError(f"기상이 {len(hours)}시간입니다({HOURS + 1}개 필요)")
    for i, h in enumerate(hours):
        missing = [k for k in HOUR_KEYS if h.get(k) is None]
        if missing:
            raise ValueError(f"기상 +{i}시간에 {missing} 값이 없습니다")
        if not 0 <= h["wind_dir"] <= 360 or h["wind_speed"] < 0 or h["precip"] < 0:
            raise ValueError(f"기상 +{i}시간 값이 범위를 벗어납니다")
        if not 0 <= h["humidity"] <= 100:
            raise ValueError(f"기상 +{i}시간 습도가 0 ~ 100이 아닙니다")
    return weather
