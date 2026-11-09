"""파일 기상: `predictions.weather` 모양 JSON 파일을 그대로 쓴다(시뮬레이션 · 미리 받아 둔 기상).

시각은 파일 그대로다 — 다른 날 기상을 넣어 시험할 수 있다. 모양만 검사한다.
시간마다 `kind`가 없으면 「파일」로 채운다.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from app.weather.base import Weather, WeatherRequest, check_weather


@dataclass(frozen=True)
class FileWeather:
    path: Path
    name: str = "file"

    def fetch(self, request: WeatherRequest) -> Weather:
        weather = json.loads(Path(self.path).read_text(encoding="utf-8"))
        for h in weather.get("hours") or []:
            h.setdefault("kind", "파일")
        return check_weather(weather)
