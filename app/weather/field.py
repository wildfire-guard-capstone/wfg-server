"""관측소 여러 곳의 실시간 기상 → ELMFIRE 기상 입력(출처 stations).

의성 시연 방식(관측소 보간)의 실시간판, B 방식.

  1. 관측소 고르기: config/weather_stations.csv 중 덱 범위 + 여유(기본 20 km) 안
  2. 관측(0시까지): AWS · ASOS 매분자료 → 시간마다 가장 센 1분 풍속 · 평균 · 풍향 · 기온 · 습도
  3. 예보(1시간 뒤부터): 관측소 자리의 기상청 단기예보(초단기실황 · 초단기예보 · 단기예보, kma)
     2 · 3은 동시에 부른다(관측소 25곳이면 한 번에 몇 초 — 차례로 부르면 1 ~ 2분)
  4. 풍속 = 예보 × 그 관측소의 최근 3시간 돌풍 비율, 0시는 관측 돌풍 → 거리 가중 보간(600 m)
  5. 발화점 칸 9시간(저장 · 판정용) = 같은 격자 · 같은 거리 가중에서 꺼낸다
     풍향 · 1분 최대 풍속 = 격자의 그 칸, 평균풍속 · 기온 · 습도 · 강수 = 그 칸 가운데로 섞은 값
     0시 표시 = 관측인 관측소의 가중치 합이 절반 이상이면 「관측」, 아니면 「예보」.
     뒤 8시간은 「예보」
계산은 spread.weather_field, 정리는 docs/ELMFIRE-기상.md.

관측소 하나가 실패하면 그 관측소만 뺀다. 남은 관측소가 min_stations보다 적으면 WeatherUnavailable
— 예측 요청이 503 WEATHER_UNAVAILABLE로 끝난다(다른 기상으로 대신하지 않는다).
"""

import csv
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Transformer

from app.weather.aws_minute import AwsMinute, hours_back
from app.weather.base import HOURS, KST, WeatherRequest, WeatherUnavailable
from app.weather.elmfire_input import (
    ElmfireWeatherInput,
    ElmfireWeatherRequest,
    cell_centre,
    floor_hour,
    site_cell,
)
from app.weather.kma import KmaForecast, grid
from spread.units import MPH_PER_MS
from spread.weather_field import (
    GUST_HOURS,
    Station,
    StationSeries,
    blend_at,
    interpolate,
    station_series,
)

STATIONS_CSV = Path(__file__).resolve().parents[2] / "config" / "weather_stations.csv"
_TO_5179 = Transformer.from_crs(4326, 5179, always_xy=True).transform
OBS_SHARE_MIN = 0.5  # 발화점 칸 0시 값 중 관측 비중이 이 이상이면 「관측」


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


def _outcome(f: Future) -> tuple[Any, Exception | None]:
    """동시 호출 결과 → (값, 실패). 기상 원천이 낸 실패만 잡고 나머지(코드 오류)는 그대로 낸다."""
    try:
        return f.result(), None
    except (WeatherUnavailable, ValueError, KeyError) as e:
        return None, e


@dataclass
class StationField:
    """ELMFIRE 기상 입력의 실시간 출처(stations). DB를 모르고 저장하지 않는다."""

    observed: Callable[[str, datetime, datetime], Mapping[datetime, Mapping]]  # 관측소 → 시간별
    forecast: Callable[[tuple[float, float], datetime], Mapping[datetime, Mapping]]  # 자리 → 시간별
    stations: list[tuple[Station, tuple[float, float]]] = field(default_factory=load_stations)
    buffer_m: float = 20_000.0
    min_stations: int = 3
    # 동시에 부를 수(1이면 차례로). 11/11 실측(25곳): 25개 5.2초 · 8개 19.1초 · 차례로 1 ~ 2분
    workers: int = 25
    name: str = "stations"
    uses_server_weather: bool = False

    def select(self, profile: Mapping) -> list[tuple[Station, tuple[float, float]]]:
        x0, y0, x1, y1 = bounds_of(profile)
        b = self.buffer_m
        return [
            s for s in self.stations if x0 - b <= s[0].x <= x1 + b and y0 - b <= s[0].y <= y1 + b
        ]

    def build(self, request: ElmfireWeatherRequest) -> ElmfireWeatherInput:
        """화선 시각 · 발화점 → 격자(덱 기상 격자) + 발화점 칸 9시간.

        1. 관측소 고르기, 관측(관측소마다) · 예보(예보 칸마다 한 번)를 동시에 부른다
        2. 관측소마다 9시간을 만든다. 실패한 관측소는 이유와 함께 뺀다
        3. 남은 관측소가 min_stations보다 적으면 WeatherUnavailable
        4. 격자 = 거리 가중 보간, 발화점 칸 9시간 = 같은 격자 · 같은 가중치에서
        """
        base_at, profile = request.base_at, request.profile
        t0 = floor_hour(base_at)
        chosen = self.select(profile)  # 1
        cell_of = {st.id: grid(*site) for st, site in chosen}
        sites = {cell_of[st.id]: site for st, site in reversed(chosen)}  # 예보 칸 → 첫 관측소 자리
        start = hours_back(base_at, GUST_HOURS - 1)
        began = time.monotonic()
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as ex:
            obs_jobs = {st.id: ex.submit(self.observed, st.id, start, base_at) for st, _ in chosen}
            fc_jobs = {key: ex.submit(self.forecast, site, base_at) for key, site in sites.items()}
            obs = {k: _outcome(f) for k, f in obs_jobs.items()}
            fcs = {k: _outcome(f) for k, f in fc_jobs.items()}
        fetch_s = round(time.monotonic() - began, 1)

        used, series, skipped = [], [], {}  # 2
        for st, _ in chosen:
            (o, o_err), (fc, fc_err) = obs[st.id], fcs[cell_of[st.id]]
            try:
                if o_err or fc_err:
                    raise o_err or fc_err
                s = station_series(o, fc, t0)
            except (WeatherUnavailable, ValueError, KeyError) as e:
                skipped[st.id] = str(e)[:120]
                continue
            used.append(st)
            series.append(s)
        if len(used) < self.min_stations:  # 3
            raise WeatherUnavailable(
                f"기상 격자에 쓸 관측소가 {len(used)}곳뿐입니다"
                f"(최소 {self.min_stations}): {skipped}"
            )

        grids = interpolate(used, series, profile)  # 4
        lead, lead_obs = lead_band(used, series, {st.id: obs[st.id][0] for st in used}, t0, profile)
        row, col = site_cell(profile, request.site)
        mix = blend_at(used, series, *cell_centre(profile, row, col))
        hours = []
        for h in range(HOURS + 1):
            values = {k: mix[k][h] for k in ("mean", "ta", "hm")}
            if missing := [k for k, v in values.items() if v is None]:
                raise WeatherUnavailable(f"발화점 칸 +{h}시간 {missing} 값이 없습니다")
            observed = h == 0 and mix["obs_share0"] >= OBS_SHARE_MIN
            hours.append(
                {
                    "at": (t0 + timedelta(hours=h)).isoformat(),
                    "kind": "관측" if observed else "예보",
                    "wind_dir": round(float(grids["wd"][h, row, col]), 1),
                    "wind_speed": round(values["mean"], 2),
                    "wind_max_1min": round(float(grids["ws"][h, row, col]) / MPH_PER_MS, 2),
                    "temp": round(values["ta"], 1),
                    "humidity": round(values["hm"], 1),
                    "precip": round(mix["rn"][h] or 0.0, 1),
                }
            )
        issued_all = (h.get("issued_at") for fc, _ in fcs.values() if fc for h in fc.values())
        issued = next((v for v in issued_all if v), None)  # 예보 발표 시각(관측소 모두 같은 회차)
        lat, lon = request.site
        weather = {
            "source": "KMA_STATIONS",
            "issued_at": issued,
            "location": {"lat": lat, "lon": lon},
            "hours": hours,
        }
        kinds = [k for s in series for k in s.kind]
        meta = {
            "source": self.name,
            "stations": [s.id for s in used],
            "skipped": skipped,
            "kinds": {k: kinds.count(k) for k in sorted(set(kinds))},
            "gust_factor": {
                s.id: round(x.gust_factor, 3) for s, x in zip(used, series, strict=True)
            },
            "forecast_grids": len(sites),
            "hour0_observed_share": round(mix["obs_share0"], 3),
            "lead_observed": lead_obs,  # 0시 1시간 앞 밴드를 관측으로 채운 관측소 수
            "site_cell": [row, col],
            "fetch_s": fetch_s,
        }
        return ElmfireWeatherInput(t0, grids, weather, meta, lead)


def lead_band(
    stations: list[Station],
    series: list[StationSeries],
    observed: Mapping[str, Mapping[datetime, Mapping] | None],
    t0: datetime,
    profile: Mapping,
) -> tuple[dict[str, np.ndarray], int]:
    """0시 1시간 앞 밴드(ElmfireWeatherInput.lead) — 시연 덱과 같은 ELMFIRE 계산을 위해.

    관측소마다 1시간 전 관측(가장 센 1분 풍속 · 풍향)을 쓰고, 없으면 그 관측소 0시 값.
    연료수분은 0시 값(1시간 차이는 결과에 거의 영향이 없다). 격자는 0시와 같은 보간.
    """
    prev, n_obs, one = t0 - timedelta(hours=1), 0, []
    for st, s in zip(stations, series, strict=True):
        o = (observed.get(st.id) or {}).get(prev) or {}
        if o.get("gust") is not None and o.get("wd") is not None:
            ws, wd, n_obs = o["gust"], o["wd"], n_obs + 1
        else:
            ws, wd = s.ws[0], s.wd[0]
        one.append(StationSeries(ws=[ws], wd=[wd], m1=[s.m1[0]], m10=[s.m10[0]], m100=[s.m100[0]]))
    grids = interpolate(stations, one, profile)
    return {k: a[0] for k, a in grids.items()}, n_obs


def kma_forecast(service_key: str) -> Callable[[tuple[float, float], datetime], dict]:
    """관측소 자리 → 시간 → {ws, wd, ta, hm, rn, issued_at}
    (기상청 단기예보 9시간, kma 원천 그대로)."""
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
                "issued_at": w.get("issued_at"),
            }
        return out

    return fetch


def station_field(kma_api_key: str, data_go_kr_api_key: str, **options: Any) -> StationField:
    """운영: 관측 = API 허브 매분자료(KMA_API_KEY),
    예보 = 공공데이터포털 단기예보(DATA_GO_KR_API_KEY)."""
    aws = AwsMinute(kma_api_key)
    return StationField(aws.fetch, kma_forecast(data_go_kr_api_key), **options)


__all__ = ["StationField", "bounds_of", "kma_forecast", "load_stations", "station_field"]
