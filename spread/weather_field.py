"""관측소 여러 곳 기상 → ELMFIRE 기상 격자(9밴드) — 의성 시연 방식의 실시간판(B 방식).

시연(의성 덱): 관측소 22곳의 1분 자료 → 시간마다 가장 센 1분 풍속(돌풍) · 풍향 · 기온 · 습도 ·
강수 → 고사연료수분 → 거리 가중 보간(IDW, 거듭제곱 2, 최소 거리 500 m) → 600 m 격자.
예측 8시간 동안에도 실제 관측 바람을 넣었다.

실시간(이 모듈): 0시까지는 시연과 같다(관측). 앞으로 8시간은 관측이 없으므로
  풍속 = 그 관측소 자리의 예보 풍속(평균) × 그 관측소의 최근 돌풍 비율(가장 센 1분 ÷ 평균)
  풍향 · 기온 · 습도 · 강수 = 예보
로 채우고, 같은 방식으로 보간한다. 예보가 없는 시간은 바로 앞 시간 값을 이어 쓴다(지속).

numpy만 쓴다. 관측 · 예보를 받아 오는 일은 app.weather.field가 한다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import exp

import numpy as np

from spread.units import MPH_PER_MS
from spread.weather_input import EMC_MAX, LAGS_H, M_MAX, M_MIN, RAIN_MM, emc_percent

HOURS = 8
IDW_POWER = 2.0
IDW_MIN_M = 500.0  # 관측소 바로 옆 칸이 한 곳 값만 갖지 않게(시연과 같음)
GUST_LO, GUST_HI = 1.0, 3.0  # 돌풍 비율 범위
GUST_HOURS = 3  # 돌풍 비율을 잴 최근 관측 시간 수
MIN_MEAN_MS = 0.5  # 평균풍속이 이보다 작은 시간은 비율 계산에서 뺀다(고요)

# 관측 한 시간: gust(가장 센 1분 풍속) · mean(평균풍속) m/s, wd °, ta ℃, hm %, rn mm
# 예보 한 시간: ws(평균풍속) m/s, wd, ta, hm, rn
Hourly = Mapping[datetime, Mapping[str, float | None]]


@dataclass(frozen=True)
class Station:
    id: str
    x: float  # EPSG:5179
    y: float
    name: str = ""


def gust_factor(obs: Hourly, until: datetime, hours: int = GUST_HOURS) -> float:
    """최근 `hours`시간(until 포함)의 Σ가장 센 1분 ÷ Σ평균. 잴 수 없으면 1(보정 없음)."""
    g = m = 0.0
    for t in [until - timedelta(hours=h) for h in range(hours)]:
        o = obs.get(t) or {}
        if o.get("gust") is not None and o.get("mean") is not None and o["mean"] >= MIN_MEAN_MS:
            g, m = g + o["gust"], m + o["mean"]
    if m == 0:
        return 1.0
    return float(min(max(g / m, GUST_LO), GUST_HI))


@dataclass
class StationSeries:
    """한 관측소의 0시 ~ 8시간 뒤(9개). kind = 관측 · 예보 · 지속."""

    ws: list[float] = field(default_factory=list)  # m/s, 돌풍 기준
    wd: list[float] = field(default_factory=list)
    m1: list[float] = field(default_factory=list)
    m10: list[float] = field(default_factory=list)
    m100: list[float] = field(default_factory=list)
    kind: list[str] = field(default_factory=list)
    gust_factor: float = 1.0


def _emc(h: Mapping[str, float | None]) -> float | None:
    if h.get("ta") is None or h.get("hm") is None:
        return None
    if (h.get("rn") or 0.0) >= RAIN_MM:
        return EMC_MAX
    return emc_percent(h["ta"], h["hm"])


def station_series(
    obs: Hourly,
    fc: Hourly,
    t0: datetime,
    moisture0: Mapping[str, float] | None = None,
    hours: int = HOURS,
) -> StationSeries:
    """관측(0시까지) · 예보(1시간 뒤부터) → 9시간.

    풍속: 0시 = 관측 돌풍, 뒤 = 예보 × 돌풍 비율(0시까지 최근 3시간). 0시 관측이 없으면 예보 × 비율.
    연료수분: 0시 값 = moisture0, 없으면 관측 기록으로 쌓고(시작 = 첫 평형수분), 그것도 없으면
    0시 평형수분. 뒤는 시간마다 평형수분 쪽으로 1 · 10 · 100시간 지연.
    """
    s = StationSeries(gust_factor=gust_factor(obs, t0))
    prev: dict[str, float | None] = {"ws": None, "wd": None, "ta": None, "hm": None, "rn": 0.0}
    rows = []
    for h in range(hours + 1):
        t = t0 + timedelta(hours=h)
        o, f = obs.get(t) or {}, fc.get(t) or {}
        if h == 0 and o.get("gust") is not None and o.get("wd") is not None:
            row = {"ws": o["gust"], "wd": o["wd"], "ta": o.get("ta"), "hm": o.get("hm"),
                   "rn": o.get("rn"), "kind": "관측"}  # fmt: skip
        elif f.get("ws") is not None and f.get("wd") is not None:
            row = {"ws": f["ws"] * s.gust_factor, "wd": f["wd"], "ta": f.get("ta"),
                   "hm": f.get("hm"), "rn": f.get("rn"), "kind": "예보"}  # fmt: skip
        else:
            row = {**prev, "kind": "지속"}
        for k in ("ta", "hm"):  # 기온 · 습도만 빠지면 앞 값
            if row[k] is None:
                row[k] = prev[k]
        prev = {k: row[k] for k in ("ws", "wd", "ta", "hm", "rn")}
        rows.append(row)
    if rows[0]["ws"] is None:
        raise ValueError("0시 풍속이 관측에도 예보에도 없습니다")

    # 연료수분
    s.ws = [round(float(r["ws"]), 3) for r in rows]
    s.wd = [float(r["wd"]) % 360 for r in rows]
    s.kind = [r["kind"] for r in rows]
    emcs = [_emc(r) for r in rows]
    known = [e for e in emcs if e is not None]
    if not known:  # 기온 · 습도가 하나도 없으면 0시 수분을 그대로 둔다
        if moisture0 is None or len(moisture0) < len(LAGS_H):
            raise ValueError("기온 · 습도도 0시 연료수분도 없습니다")
        for name in LAGS_H:
            setattr(s, name, [round(float(moisture0[name]), 3)] * (hours + 1))
        return s
    emcs = [e if e is not None else known[0] for e in emcs]
    m0 = dict(moisture0 or {})
    if len(m0) < len(LAGS_H):
        m0 = {**_spin_up(obs, t0, emcs[0]), **m0}
    for name, tau in LAGS_H.items():
        keep, m = exp(-1.0 / tau), [m0[name]]
        for e in emcs[1:]:
            m.append(e + (m[-1] - e) * keep)
        setattr(s, name, [round(min(max(v, M_MIN), M_MAX), 3) for v in m])
    return s


def _spin_up(obs: Hourly, t0: datetime, emc0: float) -> dict[str, float]:
    """0시 전 관측으로 쌓은 0시 연료수분. 기록이 없으면 0시 평형수분."""
    past = sorted(t for t in obs if t <= t0)
    emcs = [e for e in (_emc(obs[t]) for t in past) if e is not None]
    if not emcs:
        return {name: emc0 for name in LAGS_H}
    out = {}
    for name, tau in LAGS_H.items():
        keep, m = exp(-1.0 / tau), emcs[0]
        for e in emcs[1:]:
            m = e + (m - e) * keep
        out[name] = m
    return out


# ── 보간 ────────────────────────────────────────────────────────────────────


def grid_centres(profile: Mapping) -> tuple[np.ndarray, np.ndarray]:
    t = profile["transform"]
    cols = np.arange(profile["width"]) + 0.5
    rows = np.arange(profile["height"]) + 0.5
    return t.c + cols * t.a, t.f + rows * t.e


def idw_weights(
    stations: Sequence[Station], profile: Mapping, power: float = IDW_POWER,
    min_m: float = IDW_MIN_M,
) -> np.ndarray:  # fmt: skip
    """(행, 열, 관측소) 가중치, 칸마다 합 1."""
    xs, ys = grid_centres(profile)
    gx, gy = np.meshgrid(xs, ys)
    px = np.array([s.x for s in stations])
    py = np.array([s.y for s in stations])
    d = np.hypot(gx[..., None] - px, gy[..., None] - py)
    w = 1.0 / np.maximum(d, min_m) ** power
    return w / w.sum(-1, keepdims=True)


def interpolate(
    stations: Sequence[Station], series: Sequence[StationSeries], profile: Mapping
) -> dict[str, np.ndarray]:
    """관측소 시계열 → 이름 → (9, 행, 열) float32. ws = mph(ELMFIRE), 풍향은 u · v로 보간."""
    if not stations:
        raise ValueError("관측소가 없습니다")
    w = idw_weights(stations, profile)

    def blend(k: str) -> np.ndarray:
        v = np.array([getattr(s, k) for s in series], "float64").T  # (시간, 관측소)
        return np.einsum("rcs,hs->hrc", w, v)

    ws = np.array([s.ws for s in series], "float64").T
    wd = np.radians(np.array([s.wd for s in series], "float64").T)
    u = np.einsum("rcs,hs->hrc", w, -ws * np.sin(wd))
    v = np.einsum("rcs,hs->hrc", w, -ws * np.cos(wd))
    out = {
        "ws": blend("ws") * MPH_PER_MS,
        "wd": (np.degrees(np.arctan2(-u, -v)) + 360) % 360,
        "m1": blend("m1"),
        "m10": blend("m10"),
        "m100": blend("m100"),
    }
    return {k: a.astype("float32") for k, a in out.items()}
