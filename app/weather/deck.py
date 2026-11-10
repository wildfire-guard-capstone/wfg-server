"""시연 기상: 덱에 든 의성 관측 기상 → ELMFIRE 기상 입력(출처 deck, PREDICTOR_WEATHER_FIELD=deck).

덱(demo/uiseong/deck, 릴리스 deck-20261110)의 기상 래스터는 관측소 22곳(ASOS 4 + AWS 18)의 실제
관측을 600 m로 보간한 것이다 — namelist CURRENT_YEAR · HOUR_OF_YEAR 시각(2025-03-22 11:00)부터
1시간 간격(151밴드). 화선 시각을 내린 정시부터 9밴드를 잘라 쓴다. 덱 시각 밖이면 WeatherUnavailable
— 예측 요청이 503으로 끝난다(다른 기상으로 대신하지 않는다).

  ELMFIRE 격자 = namelist WS · WD · M1 · M10 · M100 래스터
                 (시간 안 가장 센 1분 풍속 · 풍향 · 연료수분)
                 + 0시 1시간 앞 밴드(lead, 덱 첫 시각이면 없음) — 시연과 같은 ELMFIRE 계산
                 (app.weather.elmfire_input.ElmfireWeatherInput.lead)
  발화점 칸 9시간(저장 · 판정용), 9시간 모두 「관측」
    바람 = 덱의 발화점 칸(평균풍속 = ws.tif, 1분 최대 · 풍향 = ELMFIRE 격자)
    기온 · 습도 · 강수 = 서버가 받은 기상(시연은 WEATHER=asos, 의성 지상관측)
    — 덱에는 기온 · 습도 래스터가 없다(연료수분으로만 들어 있다)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from app.weather.base import HOURS, KST, WeatherUnavailable
from app.weather.elmfire_input import (
    NAMELIST_KEYS,
    ElmfireWeatherInput,
    ElmfireWeatherRequest,
    floor_hour,
    namelist_value,
    site_cell,
)

MS_PER_MPH = 0.44704


def deck_start(namelist: str) -> datetime:
    """덱 기상 밴드 1의 시각(한국 시간): CURRENT_YEAR 1월 1일 0시 + HOUR_OF_YEAR시간."""
    year = int(float(namelist_value(namelist, "CURRENT_YEAR")))
    hour = int(float(namelist_value(namelist, "HOUR_OF_YEAR")))
    return datetime(year, 1, 1, tzinfo=KST) + timedelta(hours=hour)


@dataclass(frozen=True)
class DeckWeather:
    """ELMFIRE 기상 입력의 시연 출처(deck). 기온 · 습도 · 강수는 서버가 받은 기상에서 가져온다."""

    deck: Path
    mean_raster: str = "ws"  # 시간평균 풍속 래스터(규칙 엔진용, 덱 README) — 저장 · 판정의 평균풍속
    name: str = "deck"
    uses_server_weather: bool = True

    def _read(self, raster: str, bands: list[int], profile: dict[str, Any]) -> np.ndarray:
        import rasterio

        with rasterio.open(Path(self.deck) / "inputs" / f"{raster}.tif") as r:
            if (r.width, r.height) != (profile["width"], profile["height"]):
                raise ValueError(f"덱 {raster} 격자가 기상 격자와 크기가 다릅니다")
            return r.read(bands).astype("float32")

    def build(self, request: ElmfireWeatherRequest) -> ElmfireWeatherInput:
        """화선 시각 → 덱 9밴드 + 발화점 칸 9시간.

        1. 덱 기상 시작 시각 · 밴드 수로 화선 시각을 내린 정시가 덱 안인지 본다(밖이면 503)
        2. ELMFIRE 격자 5개를 9밴드 잘라 읽는다
        3. 발화점 칸: 바람은 덱, 기온 · 습도 · 강수는 서버 기상(같은 9시각이어야 한다)
        """
        import rasterio

        namelist = (Path(self.deck) / "elmfire.data").read_text(encoding="utf-8")
        files = {k: namelist_value(namelist, key) for k, key in NAMELIST_KEYS.items()}
        start, t0 = deck_start(namelist), floor_hour(request.base_at)
        with rasterio.open(Path(self.deck) / "inputs" / f"{files['ws']}.tif") as r:  # 1
            n = r.count
        offset = (t0 - start).total_seconds() / 3600
        if offset != int(offset) or not 0 <= offset <= n - (HOURS + 1):
            last = start + timedelta(hours=n - 1)
            raise WeatherUnavailable(
                f"시연 기상(덱)은 {start:%Y-%m-%d %H:%M} ~ {last:%m-%d %H:%M}만 있습니다"
                f"(요청 0시 {t0:%Y-%m-%d %H:%M})"
            )
        bands = list(range(int(offset) + 1, int(offset) + HOURS + 2))
        grids = {k: self._read(f, bands, request.profile) for k, f in files.items()}  # 2
        lead_band = bands[0] - 1 if bands[0] > 1 else None  # 0시 1시간 앞(덱 첫 시각이면 없음)
        lead = (
            {k: self._read(f, [lead_band], request.profile)[0] for k, f in files.items()}
            if lead_band
            else None
        )

        server = request.weather  # 3
        if not server or len(server.get("hours") or []) != HOURS + 1:
            raise WeatherUnavailable(
                "시연 기상에는 서버 기상(기온 · 습도 · 강수 9시간)이 필요합니다"
            )
        for h, sh in enumerate(server["hours"]):
            if datetime.fromisoformat(sh["at"]) != t0 + timedelta(hours=h):
                raise WeatherUnavailable(
                    f"서버 기상 +{h}시간 시각이 덱과 맞지 않습니다: {sh['at']}"
                )
        row, col = site_cell(request.profile, request.site)
        mean = self._read(self.mean_raster, bands, request.profile)[:, row, col] * MS_PER_MPH
        hours = [
            {
                "at": (t0 + timedelta(hours=h)).isoformat(),
                "kind": "관측",
                "wind_dir": round(float(grids["wd"][h, row, col]), 1),
                "wind_speed": round(float(mean[h]), 2),
                "wind_max_1min": round(float(grids["ws"][h, row, col]) * MS_PER_MPH, 2),
                "temp": float(sh["temp"]),
                "humidity": float(sh["humidity"]),
                "precip": float(sh["precip"]),
            }
            for h, sh in enumerate(server["hours"])
        ]
        lat, lon = request.site
        weather = {
            "source": "AWS_OBS",
            "issued_at": None,
            "location": {"lat": lat, "lon": lon},
            "hours": hours,
        }
        meta = {
            "source": self.name,
            "deck": str(self.deck),
            "deck_start": start.isoformat(),
            "bands": [bands[0], bands[-1]],
            "lead_band": lead_band,
            "temp_humidity_from": server.get("source"),
            "site_cell": [row, col],
        }
        return ElmfireWeatherInput(t0, grids, weather, meta, lead)


__all__ = ["DeckWeather", "deck_start"]
