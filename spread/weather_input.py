"""서버 기상(predictions.weather 모양) → ELMFIRE 기상 입력(시간마다 한 장).

예측 요청 때 서버가 받은 9시간 기상(0시 = 화선 관측 시각을 내림한 정시, 1시간 간격)을
DB에 저장하지 않고 그대로 ELMFIRE 입력으로 바꾼다. 자세한 근거는 docs/ELMFIRE-기상.md.

  wind_speed (m/s, 10 m)  → ws (mph)  × 2.23694 (× wind_mult). 덱 WS_AT_10M=.TRUE.이 ×0.87
  wind_dir (°, 불어옴)    → wd (°)    그대로
  temp (℃) · humidity (%) · precip (mm)
                          → m1 · m10 · m100 (%)  평형수분(Simard 1968) → 시간지연(1 · 10 · 100 h)

의성 덱(demo/uiseong/deck)의 m1_obs · m10_obs · m100_obs와 같은 식이다(관측소 기온 · 습도로 계산).
다른 점: 덱은 3월 1일부터 쌓아 온 값(m10 · m100 시작 15 %)이고, 서비스는 9시간뿐이라
m10 · m100 시작값을 0시 평형수분으로 둔다(init으로 바꿀 수 있음).

numpy만 쓴다(래스터 쓰기는 app.predict.elmfire) — API 이미지에서도 시험할 수 있다.
"""

from dataclasses import dataclass
from datetime import datetime
from math import exp
from typing import Any

import numpy as np

from spread.units import MPH_PER_MS

LAGS_H = {"m1": 1.0, "m10": 10.0, "m100": 100.0}  # 고사연료 시간지연 연료(1 · 10 · 100시간)
RAIN_MM = 0.5  # 이 이상 비가 오면 그 시간 평형수분 = 상한(젖은 연료)
EMC_MIN, EMC_MAX = 1.0, 35.0  # 평형수분 범위(%)
M_MIN, M_MAX = 2.0, 35.0  # 연료수분 범위(%)


def emc_percent(temp_c: float, rh: float) -> float:
    """평형수분(%) — Simard(1968), 미국 NFDRS의 식. 기온은 ℉로 바꿔 넣는다."""
    tf = temp_c * 9 / 5 + 32
    if rh < 10:
        e = 0.03229 + 0.281073 * rh - 0.000578 * rh * tf
    elif rh < 50:
        e = 2.22749 + 0.160107 * rh - 0.01478 * tf
    else:
        e = 21.0606 + 0.005565 * rh**2 - 0.00035 * rh * tf - 0.483199 * rh
    return min(max(e, EMC_MIN), EMC_MAX)


def dead_fuel_moisture(
    hours: list[dict[str, Any]], init: dict[str, float] | None = None
) -> dict[str, list[float]]:
    """시간마다 m1 · m10 · m100(%). m(i) = E(i) + (m(i−1) − E(i)) · exp(−1/τ).

    init = 0시 값(없으면 0시 평형수분 — 앞선 기상 기록이 없을 때의 가정).
    """
    emc = [
        EMC_MAX if h["precip"] >= RAIN_MM else emc_percent(h["temp"], h["humidity"]) for h in hours
    ]
    out: dict[str, list[float]] = {}
    for name, tau in LAGS_H.items():
        keep = exp(-1.0 / tau)
        m = [(init or {}).get(name, emc[0])]
        for e in emc[1:]:
            m.append(e + (m[-1] - e) * keep)
        out[name] = [round(min(max(v, M_MIN), M_MAX), 3) for v in m]
    return out


@dataclass(frozen=True)
class ElmfireWeather:
    """ELMFIRE 기상 밴드(밴드 1 = t0).

    값은 시간마다 하나 — 기준점 한 곳의 기상을 영역 전체에 쓴다.
    """

    t0: datetime  # 밴드 1의 시각(= 기상 0시)
    ws_mph: list[float]
    wd_deg: list[float]
    m1: list[float]
    m10: list[float]
    m100: list[float]

    @property
    def bands(self) -> dict[str, list[float]]:
        return {
            "ws": self.ws_mph,
            "wd": self.wd_deg,
            "m1": self.m1,
            "m10": self.m10,
            "m100": self.m100,
        }

    def grids(self, shape: tuple[int, int]) -> dict[str, np.ndarray]:
        """이름 → (밴드, 행, 열) float32. 영역 안은 시간마다 같은 값."""
        return {
            k: np.broadcast_to(np.asarray(v, "float32")[:, None, None], (len(v), *shape)).copy()
            for k, v in self.bands.items()
        }


def to_elmfire(
    weather: dict[str, Any], wind_mult: float = 1.0, init: dict[str, float] | None = None
) -> ElmfireWeather:
    """서버 기상(hours 9개, check_weather를 통과한 모양) → ELMFIRE 기상 밴드."""
    hours = weather["hours"]
    if not hours:
        raise ValueError("기상 hours가 비었습니다")
    t0 = datetime.fromisoformat(hours[0]["at"])
    for i, h in enumerate(hours):  # 1시간 간격이어야 밴드 i = i시간 뒤
        if (datetime.fromisoformat(h["at"]) - t0).total_seconds() != i * 3600:
            raise ValueError(f"기상 +{i}시간의 시각이 1시간 간격이 아닙니다: {h['at']}")
    moisture = dead_fuel_moisture(hours, init)
    return ElmfireWeather(
        t0=t0,
        ws_mph=[round(h["wind_speed"] * wind_mult * MPH_PER_MS, 3) for h in hours],
        wd_deg=[float(h["wind_dir"]) % 360 for h in hours],
        **moisture,
    )


def hour_of_year(t: datetime) -> int:
    """ELMFIRE HOUR_OF_YEAR(1월 1일 0시 = 0). 의성 덱: 2025-03-22 11:00 = 1931."""
    return (t.timetuple().tm_yday - 1) * 24 + t.hour
