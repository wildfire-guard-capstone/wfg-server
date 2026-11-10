"""관측소 여러 곳의 실시간 기상 → ELMFIRE 기상 격자(의성 시연 방식의 실시간판, B 방식).

  1. 관측소 고르기: config/weather_stations.csv 중 덱 범위 + 여유(기본 20 km) 안
  2. 관측(0시까지): AWS · ASOS 매분자료 → 시간마다 가장 센 1분 풍속 · 평균 · 풍향 · 기온 · 습도
  3. 예보(1시간 뒤부터): 관측소 자리의 기상청 단기예보(초단기실황 · 초단기예보 · 단기예보, kma)
  4. 풍속 = 예보 × 그 관측소의 최근 3시간 돌풍 비율, 0시는 관측 돌풍 → 거리 가중 보간(600 m)
계산은 spread.weather_field, 정리는 docs/ELMFIRE-기상.md.

관측소 하나가 실패하면 그 관측소만 뺀다. 남은 관측소가 min_stations보다 적으면 WeatherUnavailable
— ELMFIRE 예측은 그때 기준점 한 곳 기상으로 돌아간다(app.predict.elmfire).
"""

import csv
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Transformer

from app.weather.aws_minute import AwsMinute, hours_back
from app.weather.base import KST, WeatherRequest, WeatherUnavailable
from app.weather.kma import KmaForecast, grid
from spread.weather_field import GUST_HOURS, Station, interpolate, station_series

STATIONS_CSV = Path(__file__).resolve().parents[2] / "config" / "weather_stations.csv"
_TO_5179 = Transformer.from_crs(4326, 5179, always_xy=True).transform


def load_stations(path: Path = STATIONS_CSV) -> list[tuple[Station, tuple[float, float]]]:
    """(관측소 EPSG:5179, (위도, 경도)) 목록."""
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lat, lon = float(r["lat"]), float(r["lon"])
            x, y = _TO_5179(lon, lat)
            out.append((Station(r["stn"], x, y, r["name"]), (lat, lon)))
    return out


def bounds_of(profile: Mapping) -> tuple[float, float, float, float]:
    t = profile["transform"]
    x0, y1 = t.c, t.f
    return x0, y1 + t.e * profile["height"], x0 + t.a * profile["width"], y1


@dataclass
class StationField:
    """ELMFIRE 예측기가 부르는 기상 격자 제공자. DB를 모르고 저장하지 않는다."""

    observed: Callable[[str, datetime, datetime], Mapping[datetime, Mapping]]  # 관측소 → 시간별
    forecast: Callable[[tuple[float, float], datetime], Mapping[datetime, Mapping]]  # 자리 → 시간별
    stations: list[tuple[Station, tuple[float, float]]] = field(default_factory=load_stations)
    buffer_m: float = 20_000.0
    min_stations: int = 3

    def select(self, profile: Mapping) -> list[tuple[Station, tuple[float, float]]]:
        x0, y0, x1, y1 = bounds_of(profile)
        b = self.buffer_m
        return [
            s for s in self.stations if x0 - b <= s[0].x <= x1 + b and y0 - b <= s[0].y <= y1 + b
        ]

    def build(self, base_at: datetime, profile: Mapping) -> tuple[dict[str, np.ndarray], dict]:
        """base_at(화선 시각) → (이름 → (9, 행, 열), meta). 격자 = 덱 기상 격자(profile)."""
        t0 = base_at.astimezone(KST).replace(minute=0, second=0, microsecond=0)
        used, series, skipped = [], [], {}
        fc_cache: dict[tuple[int, int], Mapping] = {}
        for st, site in self.select(profile):
            try:
                obs = self.observed(st.id, hours_back(base_at, GUST_HOURS - 1), base_at)
                key = grid(*site)
                if key not in fc_cache:
                    fc_cache[key] = self.forecast(site, base_at)
                s = station_series(obs, fc_cache[key], t0)
            except (WeatherUnavailable, ValueError, KeyError) as e:
                skipped[st.id] = str(e)[:120]
                continue
            used.append(st)
            series.append(s)
        if len(used) < self.min_stations:
            raise WeatherUnavailable(
                f"기상 격자에 쓸 관측소가 {len(used)}곳뿐입니다"
                f"(최소 {self.min_stations}): {skipped}"
            )
        grids = interpolate(used, series, profile)
        kinds = [k for s in series for k in s.kind]
        meta = {
            "stations": [s.id for s in used],
            "skipped": skipped,
            "kinds": {k: kinds.count(k) for k in sorted(set(kinds))},
            "gust_factor": {
                s.id: round(x.gust_factor, 3) for s, x in zip(used, series, strict=True)
            },
            "forecast_grids": len(fc_cache),
        }
        return grids, meta


def kma_forecast(service_key: str) -> Callable[[tuple[float, float], datetime], dict]:
    """관측소 자리 → 시간 → {ws, wd, ta, hm, rn}(기상청 단기예보 9시간, kma 원천 그대로)."""
    provider = KmaForecast(service_key)

    def fetch(site: tuple[float, float], base_at: datetime) -> dict[datetime, dict[str, Any]]:
        w = provider.fetch(WeatherRequest(site, base_at))
        out = {}
        for h in w["hours"]:
            t = datetime.fromisoformat(h["at"]).astimezone(KST)
            out[t] = {
                "ws": h["wind_speed"],
                "wd": h["wind_dir"],
                "ta": h["temp"],
                "hm": h["humidity"],
                "rn": h["precip"],
            }
        return out

    return fetch


def station_field(kma_api_key: str, data_go_kr_api_key: str, **options: Any) -> StationField:
    """운영: 관측 = API 허브 매분자료(KMA_API_KEY),
    예보 = 공공데이터포털 단기예보(DATA_GO_KR_API_KEY)."""
    aws = AwsMinute(kma_api_key)
    return StationField(aws.fetch, kma_forecast(data_go_kr_api_key), **options)


__all__ = ["StationField", "bounds_of", "load_stations", "station_field"]
