"""ELMFIRE 기상 입력 — 약속(인터페이스)과 갈아 끼우는 출처.

어떤 출처든 ElmfireWeatherRequest를 받아 ElmfireWeatherInput 하나를 만든다. 예측 요청 때(PRED-01)
서버가 출처로 입력을 만들고 — 못 만들면 WeatherUnavailable → 요청이 503 WEATHER_UNAVAILABLE,
다른 기상으로 대신하지 않는다 — ELMFIRE 예측(app.predict.elmfire)은 이 입력만 받아 돌린다.
예측은 출처를 모른다.

출처(설정 PREDICTOR_WEATHER_FIELD, 예측마다 이름으로 바꿀 수 있다):
  stations  실시간: 관측소 여러 곳 매분자료(0시까지) + 단기예보(뒤 8시간) → 600 m 보간
            (app.weather.field)
  deck      시연: 덱에 든 의성 관측 기상(2025-03-22 11:00부터)을 화선 시각부터 9시간
            (app.weather.deck)

입력 = 격자(ELMFIRE) + 발화점 칸 9시간(저장 · 규칙 엔진 · 프론트) + 출처 정보(이벤트 로그).
발화점 칸 값은 ELMFIRE 격자와 같은 계산에서 꺼낸다 — 판정에 쓰는 바람과 확산에 쓴 바람이 같은
관측소 · 시각 · 비율에서 나온다. 평균풍속 wind_speed는 판정 · 화면, 시간 안 가장 센 1분 풍속
wind_max_1min은 ELMFIRE 입력 기록(DB에만, PRED-04 응답에서 뺀다).
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from pyproj import Transformer

from app.weather.base import HOURS, KST, Weather, check_weather

# 격자 이름 → 덱 namelist에서 그 래스터 이름을 가리키는 키.
# ws = mph(시간 안 가장 센 1분 풍속, 덱 A′ 기준), wd = °(불어오는 쪽), m1 · m10 · m100 = %
NAMELIST_KEYS = {
    "ws": "WS_FILENAME",
    "wd": "WD_FILENAME",
    "m1": "M1_FILENAME",
    "m10": "M10_FILENAME",
    "m100": "M100_FILENAME",
}
GRID_KEYS = tuple(NAMELIST_KEYS)
SOURCES = ("stations", "deck")
_TO_5179 = Transformer.from_crs(4326, 5179, always_xy=True).transform


@dataclass(frozen=True)
class ElmfireWeatherRequest:
    base_at: datetime  # 화선 관측 시각(P0). 기상 0시 = 이를 내린 정시
    site: tuple[float, float]  # 발화점 위도 · 경도 — 저장 · 판정용 9시간을 꺼낼 칸
    profile: Mapping[str, Any]  # 덱 기상 격자(transform · width · height) — 격자를 이 크기로
    weather: Weather | None = None  # 서버가 받은 기준점 기상(deck은 기온 · 습도 · 강수에 쓴다)


@dataclass(frozen=True)
class ElmfireWeatherInput:
    t0: datetime  # 기상 0시(한국 시간) = grids 첫 밴드 시각
    grids: dict[str, np.ndarray]  # GRID_KEYS → (9, 행, 열) float32, 덱 기상 격자
    weather: Weather  # 발화점 칸 9시간(명세 Weather + hours[].wind_max_1min) → predictions.weather
    meta: dict[str, Any] = field(default_factory=dict)  # 출처 · 관측소 · 덱 밴드 → 이벤트 로그
    # 0시 1시간 앞(t0 − 1시간) 밴드: GRID_KEYS → (행, 열). 있으면 ELMFIRE 밴드 1로 앞에 붙인다.
    # ELMFIRE는 시각 T의 기상을 밴드 floor(T/Δ) · floor(T/Δ)+1로 F = (T − (ITLO−1)Δ)/Δ 만큼
    # 섞는데(elmfire_level_set.f90 842 ~ 846), 첫 1시간만 F가 0 ~ 1(보간)이고 그 뒤는 1 ~ 2(앞
    # 두 밴드 추세를 연장)다. 시연 덱(밴드 1 = 11:00)은 1보부터 늘 뒤쪽으로 계산됐고 A′ 성능도
    # 그 결과다. 0시부터 자르면 첫 1시간이 보간으로 바뀌어 결과가 달라지므로(1보 P1 116 → 107 ha)
    # 한 밴드를 앞에 붙여 시연과 같은 계산을 만든다. 없으면(덱 첫 시각 등) 0시부터 — 시연도 같다
    lead: dict[str, np.ndarray] | None = None


class ElmfireWeatherSource(Protocol):
    name: str
    uses_server_weather: bool  # True면 서버가 기준점 기상을 먼저 받아 request.weather로 넘긴다

    def build(self, request: ElmfireWeatherRequest) -> ElmfireWeatherInput: ...


def elmfire_bands(inp: "ElmfireWeatherInput") -> tuple[dict[str, np.ndarray], datetime]:
    """ELMFIRE에 넣을 밴드(lead가 있으면 앞에 붙임)와 밴드 1의 시각."""
    if inp.lead is None:
        return inp.grids, inp.t0
    stacked = {k: np.concatenate([inp.lead[k][None], inp.grids[k]]) for k in GRID_KEYS}
    return stacked, inp.t0 - timedelta(hours=1)


def floor_hour(t: datetime) -> datetime:
    """한국 시간으로 내린 정시(기상 0시)."""
    return t.astimezone(KST).replace(minute=0, second=0, microsecond=0)


def namelist_value(text: str, key: str) -> str:
    """`KEY = 'value' ! 주석` → value(따옴표 없이)."""
    for line in text.splitlines():
        left, _, right = line.partition("=")
        if left.strip() == key:
            return right.split("!")[0].strip().strip("'\"")
    raise KeyError(key)


def deck_weather_profile(deck: Path) -> dict[str, Any]:
    """덱 기상 격자(namelist WS_FILENAME 래스터)의 profile. rasterio는 여기서만 부른다."""
    import rasterio

    text = (Path(deck) / "elmfire.data").read_text(encoding="utf-8")
    name = namelist_value(text, NAMELIST_KEYS["ws"])
    with rasterio.open(Path(deck) / "inputs" / f"{name}.tif") as r:
        return r.profile.copy()


def site_cell(profile: Mapping[str, Any], site: tuple[float, float]) -> tuple[int, int]:
    """발화점(위도, 경도)이 든 칸(행, 열). 격자 밖이면 ValueError."""
    lat, lon = site
    x, y = _TO_5179(lon, lat)
    t = profile["transform"]
    col, row = math.floor((x - t.c) / t.a), math.floor((y - t.f) / t.e)
    if not (0 <= row < profile["height"] and 0 <= col < profile["width"]):
        raise ValueError(f"발화점 {site}이 덱 기상 격자 밖입니다")
    return row, col


def cell_centre(profile: Mapping[str, Any], row: int, col: int) -> tuple[float, float]:
    """칸 가운데 EPSG:5179 좌표 — 격자를 보간한 자리와 같다(spread.weather_field.grid_centres)."""
    t = profile["transform"]
    return t.c + (col + 0.5) * t.a, t.f + (row + 0.5) * t.e


def check_input(inp: ElmfireWeatherInput, profile: Mapping[str, Any]) -> ElmfireWeatherInput:
    """ELMFIRE에 넘기기 전 모양 검사.

    1. 격자 5개가 다 있고 (9, 행, 열) = 덱 기상 격자 크기, 빈 값 없음
    2. 풍속 0 이상 · 풍향 0 ~ 360 · 연료수분 0 ~ 100 %
    3. 발화점 9시간이 명세 Weather 모양이고 첫 시각 = 격자 0시
    """
    shape = (HOURS + 1, profile["height"], profile["width"])
    for k in GRID_KEYS:  # 1
        a = inp.grids.get(k)
        if a is None or a.shape != shape:
            got = None if a is None else a.shape
            raise ValueError(f"ELMFIRE 기상 {k} 격자가 {shape}가 아닙니다: {got}")
        if not np.isfinite(a).all():
            raise ValueError(f"ELMFIRE 기상 {k} 격자에 빈 값이 있습니다")
        if inp.lead is not None:
            b = inp.lead.get(k)
            if b is None or b.shape != shape[1:] or not np.isfinite(b).all():
                got = None if b is None else b.shape
                raise ValueError(f"ELMFIRE 기상 {k} 앞 밴드가 {shape[1:]}가 아닙니다: {got}")
    g, _ = elmfire_bands(inp)  # 2
    moisture_ok = all(((g[k] >= 0) & (g[k] <= 100)).all() for k in ("m1", "m10", "m100"))
    if (g["ws"] < 0).any() or ((g["wd"] < 0) | (g["wd"] > 360)).any() or not moisture_ok:
        raise ValueError("ELMFIRE 기상 격자 값이 범위를 벗어납니다")
    check_weather(inp.weather)  # 3
    if datetime.fromisoformat(inp.weather["hours"][0]["at"]) != inp.t0:
        raise ValueError(f"발화점 9시간의 0시와 격자 0시({inp.t0})가 다릅니다")
    return inp


__all__ = [
    "GRID_KEYS",
    "NAMELIST_KEYS",
    "SOURCES",
    "ElmfireWeatherInput",
    "ElmfireWeatherRequest",
    "ElmfireWeatherSource",
    "cell_centre",
    "check_input",
    "deck_weather_profile",
    "elmfire_bands",
    "floor_hour",
    "namelist_value",
    "site_cell",
]
