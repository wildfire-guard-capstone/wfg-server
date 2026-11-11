"""ELMFIRE 예측: 화선 + 기상 입력으로 ELMFIRE를 돌려 P1 ~ P8을 만든다.

기상은 여기서 받지 않는다. 예측 요청 때 서버가 출처(stations 실시간 · deck 시연)로 만든
ElmfireWeatherInput(app.weather.elmfire_input)을 PredictionRequest.elmfire_weather로 넘긴다
— 못 만들면 요청이 503 WEATHER_UNAVAILABLE. 이 예측은 출처를 모른다.

흐름(DB에 아무것도 저장하지 않는다 — 저장은 services가 결과를 받아서 한다):
  1. 입력 검사: 격자 5개 × 9밴드가 덱 기상 격자 크기인지, 발화점 9시간 모양
  2. 실행 폴더에만 기상 래스터(wx_ws · wx_wd · wx_m1 · wx_m10 · wx_m100, 9밴드)를 쓰고
     namelist의 WS_FILENAME … · HOUR_OF_YEAR를 바꾼다. 지형 · 연료는 덱 것을 그대로 링크한다
  3. 시작 = 화선(면, phi) 또는 발화점(점). 시작 시각 = 기상 0시 뒤 (base_at − 0시)초
  4. 도달시간 → P1 ~ P8 면(EPSG:5179, 구멍 메움, 누적) — spread.perimeter_run.ranges
  5. 결과 기상 = 입력의 발화점 칸 9시간 — 규칙 엔진 · 제안서 · 프론트가 ELMFIRE와 같은
     계산의 값을 쓴다

덱 = demo/uiseong/deck 모양의 폴더(elmfire.data + inputs/*.tif, EPSG:5179). 지금은 의성 범위
(x 1069200 ~ 1176600, y 1798200 ~ 1860600)라 그 밖의 화선은 ValueError로 거절한다.
ELMFIRE · rasterio · scipy가 있는 곳(worker 이미지 · GPU 서버)에서 돈다. rasterio는 함수 안에서만
불러 API 이미지에서도 이 파일을 불러올 수 있다.

명령(DB 없이 GeoJSON만, app.predict.run_elmfire):
  python -m app.predict.run_elmfire --deck ~/wfg/demo/deck --start p0.geojson \\
      --base-at 2025-03-22T12:50+09:00 --source deck --weather asos.json --out p1_p8.geojson
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
from app.weather.base import KST
from app.weather.elmfire_input import (
    GRID_KEYS,
    NAMELIST_KEYS,
    check_input,
    deck_weather_profile,
    elmfire_bands,
)
from spread import perimeter_run as pr
from spread.weather_input import hour_of_year

DECK_EPSG = 5179


def weather_namelist(t0: datetime) -> dict[str, str]:
    """덱 namelist에서 바꿀 값: 기상 파일 이름(실행 폴더의 wx_*) · 기상 0시의 연도와 시각
    (ELMFIRE 일사 계산)."""
    out = {key: f"'wx_{name}'" for name, key in NAMELIST_KEYS.items()}
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
    adj: float = 1.0  # 확산속도 배율(A′ 덱 = 1, 보정 없음)
    env: dict = field(default_factory=dict)  # PATH · PROJ_DATA · GDAL_DATA
    timeout_s: float | None = None  # ELMFIRE 1회 시간 제한(넘으면 TimeoutError). None = 제한 없음
    name: str = "elmfire"

    def _runs(self) -> Path:
        return Path(self.work_dir) if self.work_dir else Path(self.deck).parent / "runs"

    def predict(self, request: PredictionRequest) -> PredictionResult:
        inp = request.elmfire_weather
        if inp is None:
            raise ValueError(
                "ELMFIRE 기상 입력이 없습니다 — 예측 요청 때 출처(stations · deck)로 만들어"
                " 넘깁니다"
            )
        base_at = request.base_at if request.base_at.tzinfo else request.base_at.replace(tzinfo=KST)

        deck = pr.Deck(Path(self.deck), self.elmfire, self.env)
        profile, _ = deck.grid()
        if profile["crs"] is None or profile["crs"].to_epsg() != DECK_EPSG:
            raise ValueError(f"덱 좌표계가 EPSG:{DECK_EPSG}가 아닙니다: {profile['crs']}")
        check_inside(request.start, profile)
        wx_profile = deck_weather_profile(Path(self.deck))  # 1
        check_input(inp, wx_profile)
        if not 0 <= (base_at - inp.t0).total_seconds() < 3600:
            raise ValueError(f"기상 0시({inp.t0})와 화선 시각({base_at})이 맞지 않습니다")
        bands, band1 = elmfire_bands(inp)  # 앞 밴드가 있으면 밴드 1 = 0시 − 1시간
        t_start_s = (base_at - band1).total_seconds()
        wx_profile.update(compress="deflate")
        own = {f"wx_{k}": (bands[k], wx_profile) for k in GRID_KEYS}  # 2

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
                namelist=weather_namelist(band1),
                timeout_s=self.timeout_s,
            )
            slices = pr.ranges(res, request.start, HOURS)[1:]  # 4
        finally:
            if not self.keep_runs:
                shutil.rmtree(run_dir, ignore_errors=True)
        meta: dict[str, Any] = {
            "deck": str(self.deck),
            "adj": self.adj,
            "t_start_s": t_start_s,
            "lead_band": inp.lead is not None,
            "wall_s": round(res.wall_s, 1),
            "weather_input": inp.meta,  # 출처 · 관측소 · 덱 밴드
        }
        if self.keep_runs:
            meta["run_dir"] = str(run_dir)
        return PredictionResult(slices, inp.weather, self.name, meta)  # 5


def check_inside(start: BaseGeometry, profile: dict) -> None:
    """시작 형상이 덱 범위 안인지. 밖이면 ELMFIRE가 빈 결과를 내므로 미리 거절한다."""
    t = profile["transform"]
    xmin, ymax = t.c, t.f
    xmax, ymin = xmin + t.a * profile["width"], ymax + t.e * profile["height"]
    if not box(xmin, ymin, xmax, ymax).contains(start):
        raise ValueError(
            f"화선이 덱 범위(x {xmin:.0f} ~ {xmax:.0f}, y {ymin:.0f} ~ {ymax:.0f}) 밖입니다"
        )
