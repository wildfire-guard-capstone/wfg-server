"""ELMFIRE 예측: 요청에 들어온 화선 · 기상으로 ELMFIRE를 돌려 P1 ~ P8을 만든다.

흐름(DB에 아무것도 저장하지 않는다 — 저장은 services가 결과를 받아서 한다):
  1. 요청 기상(9시간, predictions.weather 모양) → ELMFIRE 기상 밴드(spread.weather_input)
     풍속 m/s → mph, 풍향 그대로, 기온 · 습도 · 강수 → 고사연료수분 m1 · m10 · m100
  2. 실행 폴더에만 기상 래스터(wx_ws · wx_wd · wx_m1 · wx_m10 · wx_m100, 9밴드)를 쓰고
     namelist의 WS_FILENAME … · HOUR_OF_YEAR를 바꾼다. 지형 · 연료는 덱 것을 그대로 링크한다
  3. 시작 = 화선(면, phi) 또는 발화점(점). 시작 시각 = 기상 0시 뒤 (base_at − 0시)초
  4. 도달시간 → P1 ~ P8 면(EPSG:5179, 구멍 메움, 누적) — spread.perimeter_run.ranges

덱 = demo/uiseong/deck 모양의 폴더(elmfire.data + inputs/*.tif, EPSG:5179). 지금은 의성 범위
(x 1069200 ~ 1176600, y 1798200 ~ 1860600)라 그 밖의 화선은 ValueError로 거절한다.
ELMFIRE · rasterio · scipy가 있는 곳(GPU 서버 conda 환경)에서 돈다. rasterio는 함수 안에서만 불러
API 이미지에서도 이 파일을 불러올 수 있다.

명령(DB 없이 GeoJSON만, app.predict.run_elmfire):
  python -m app.predict.run_elmfire --deck ~/wfg/demo/deck --start p0.geojson \\
      --base-at 2025-03-22T12:50+09:00 --weather weather.json --out p1_p8.geojson
"""

import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from app.predict.base import HOURS, PredictionRequest, PredictionResult
from app.weather.base import KST, WeatherRequest, check_weather
from app.weather.fixed import FixedWeather
from spread import perimeter_run as pr
from spread.weather_input import ElmfireWeather, hour_of_year, to_elmfire

DECK_EPSG = 5179
# 실행 폴더에 새로 쓰는 기상 래스터 이름 → 바꿀 namelist 키
WX_FILES = {
    "ws": "WS_FILENAME",
    "wd": "WD_FILENAME",
    "m1": "M1_FILENAME",
    "m10": "M10_FILENAME",
    "m100": "M100_FILENAME",
}


def namelist_value(text: str, key: str) -> str:
    """`KEY = 'value' ! 주석` → value(따옴표 없이)."""
    for line in text.splitlines():
        left, _, right = line.partition("=")
        if left.strip() == key:
            return right.split("!")[0].strip().strip("'\"")
    raise KeyError(key)


def weather_namelist(wx: ElmfireWeather, t0: datetime) -> dict[str, str]:
    """덱 namelist에서 바꿀 값: 기상 파일 이름 · 기상 0시의 연도와 시각(ELMFIRE 일사 계산)."""
    out = {key: f"'wx_{name}'" for name, key in WX_FILES.items()}
    out["DT_METEOROLOGY"] = "3600.0"
    out["CURRENT_YEAR"] = str(t0.year)
    out["HOUR_OF_YEAR"] = str(hour_of_year(t0))
    return out


@dataclass(frozen=True)
class ElmfirePredictor:
    deck: Path
    elmfire: str = "elmfire_1.1"
    work_dir: Path | None = None  # 실행 폴더를 만들 곳(없으면 덱 옆 runs/)
    keep_runs: bool = False  # 실행 폴더를 남길지(확인용)
    wind_mult: float = 1.0  # 풍속 배율(예보 평균풍속 ↔ 덱의 돌풍 차이를 시험할 때)
    adj: float = 1.0  # 확산속도 배율(A′ 덱 = 1, 보정 없음)
    env: dict = field(default_factory=dict)  # PATH · PROJ_DATA · GDAL_DATA
    # 0시 고사연료수분(%) {"m10": …, "m100": …}. 없으면 0시 평형수분(앞선 기상 기록이 없을 때)
    moisture_init: dict[str, float] | None = None
    name: str = "elmfire"

    def _runs(self) -> Path:
        return Path(self.work_dir) if self.work_dir else Path(self.deck).parent / "runs"

    def predict(self, request: PredictionRequest) -> PredictionResult:
        import rasterio

        weather = request.weather or FixedWeather().fetch(
            WeatherRequest(request.site, request.base_at)
        )
        check_weather(weather)
        wx = to_elmfire(weather, self.wind_mult, self.moisture_init)
        t0 = wx.t0 if wx.t0.tzinfo else wx.t0.replace(tzinfo=KST)
        base_at = request.base_at if request.base_at.tzinfo else request.base_at.replace(tzinfo=KST)
        t_start_s = (base_at - t0).total_seconds()
        if not 0 <= t_start_s < 3600:
            raise ValueError(f"기상 0시({t0})와 화선 시각({base_at})이 맞지 않습니다")

        deck = pr.Deck(Path(self.deck), self.elmfire, self.env)
        profile, _ = deck.grid()
        if profile["crs"] is None or profile["crs"].to_epsg() != DECK_EPSG:
            raise ValueError(f"덱 좌표계가 EPSG:{DECK_EPSG}가 아닙니다: {profile['crs']}")
        check_inside(request.start, profile)

        ws_name = namelist_value(deck.namelist, "WS_FILENAME")
        with rasterio.open(Path(self.deck) / "inputs" / f"{ws_name}.tif") as r:
            wx_profile = r.profile.copy()
            wx_shape = (r.height, r.width)
        wx_profile.update(compress="deflate")
        own = {f"wx_{k}": (grid, wx_profile) for k, grid in wx.grids(wx_shape).items()}

        run_dir = self._runs() / f"pred_{base_at:%Y%m%d_%H%M}_{uuid.uuid4().hex[:8]}"
        try:
            res = pr.run(
                deck,
                run_dir,
                t_start_s,
                HOURS,
                request.start,
                self.adj,
                own_inputs=own,
                namelist=weather_namelist(wx, t0),
            )
            slices = pr.ranges(res, request.start, HOURS)[1:]
        finally:
            if not self.keep_runs:
                shutil.rmtree(run_dir, ignore_errors=True)
        meta: dict[str, Any] = {
            "deck": str(self.deck),
            "adj": self.adj,
            "wind_mult": self.wind_mult,
            "t_start_s": t_start_s,
            "wall_s": round(res.wall_s, 1),
            "elmfire_weather": wx.bands,  # 실제로 넣은 값(mph · ° · %)
            "moisture_init": self.moisture_init or "0시 평형수분",
        }
        if self.keep_runs:
            meta["run_dir"] = str(run_dir)
        # 기상은 받은 그대로 돌려준다 — 예측과 제안이 같은 기상을 쓴다(바꾼 값은 meta에)
        return PredictionResult(slices, weather, self.name, meta)


def check_inside(start: BaseGeometry, profile: dict) -> None:
    """시작 형상이 덱 범위 안인지. 밖이면 ELMFIRE가 빈 결과를 내므로 미리 거절한다."""
    t = profile["transform"]
    xmin, ymax = t.c, t.f
    xmax, ymin = xmin + t.a * profile["width"], ymax + t.e * profile["height"]
    if not box(xmin, ymin, xmax, ymax).contains(start):
        raise ValueError(
            f"화선이 덱 범위(x {xmin:.0f} ~ {xmax:.0f}, y {ymin:.0f} ~ {ymax:.0f}) 밖입니다"
        )
