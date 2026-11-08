"""매번 바뀌는 값 8개(기상 7 + 연료 수분함량): 시간별 값 → P5 위 등급.

시각(README W-1, 11/8 확정): 규칙 엔진의 0 ~ 5시간 평균(rules MEAN_HOURS)과 같은 6개 시각
0 · 1 · 2 · 3 · 4 · 5시간 뒤의 값을 칸마다 등급으로 바꾼 뒤 평균한다(등급 먼저, 사안 3).
- 강수량: 더해지는 값이라 P5 기간(0시 ~ 5시간 뒤)의 1시간 강수 5개(1 ~ 5시간 뒤 값)를 합한 뒤 등급
- 7일 누적 강수량: 0시까지 지난 168시간 관측 합(시각 하나)
mode="at_p5"면 5시간 뒤 한 시각만 쓴다(강수량은 그 1시간 값) — 비교용.

값 하나는 한 점 값(float, 관측소 · 예보 격자 1칸) 또는 격자(`GridField`, 예보 격자 ·
ELMFIRE 기상 600 m)다.
격자면 P5에 걸친 칸의 겹침 비율로 등급을 가중평균한다.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from shapely.geometry.base import BaseGeometry

from risk.criteria import grade
from risk.overlap import Grid, Weights, grid_weights, weighted_mean

HOURS = (0, 1, 2, 3, 4, 5)
RAIN_HOURS = (1, 2, 3, 4, 5)  # 1시간 강수는 그 시각까지 1시간 동안 내린 양 → (0시, 5시간 뒤]
MODES = ("mean_0_5", "at_p5")


@dataclass(frozen=True)
class GridField:
    values: np.ndarray  # (h, w), NaN = 값 없음
    grid: Grid


Field = float | GridField | None


@dataclass(frozen=True)
class Summary:
    grade: float | None
    value: float | None  # 화면용 대표값(평균 또는 합)
    hours: tuple[int, ...]
    detail: list  # 시각별 (시간, 값, 등급)


class _WeightCache:
    def __init__(self, geom: BaseGeometry):
        self.geom = geom
        self._by_grid: dict[Grid, Weights] = {}

    def get(self, grid: Grid) -> Weights:
        if grid not in self._by_grid:
            self._by_grid[grid] = grid_weights(self.geom, grid)
        return self._by_grid[grid]


def _field_grade(key: str, f: Field, wc: _WeightCache) -> tuple[float | None, float | None]:
    if f is None:
        return None, None
    if isinstance(f, GridField):
        w = wc.get(f.grid)
        return weighted_mean(grade(key, f.values), w), weighted_mean(f.values, w)
    if isinstance(f, float | int) and not math.isnan(f):
        return float(grade(key, [f])[0]), float(f)
    return None, None


def _sum_fields(fields: Sequence[Field]) -> Field:
    if any(f is None for f in fields):
        return None
    if all(isinstance(f, GridField) for f in fields):
        g = fields[0].grid
        if any(f.grid != g for f in fields):
            raise ValueError("강수 격자가 시각마다 다르다")
        return GridField(np.sum([f.values for f in fields], axis=0), g)
    if any(isinstance(f, GridField) for f in fields):
        raise ValueError("강수가 시각마다 점 · 격자로 섞였다")
    return float(sum(fields))


def summarize(
    key: str, by_hour: dict[int, Field], geom: BaseGeometry, mode: str = "mean_0_5", wc=None
) -> Summary:
    """시각별 값(0시 뒤 몇 시간 → 값) → P5 위 등급 하나.

    1. 강수량: P5 기간 강수 합(mean_0_5) 또는 5시간 뒤 1시간 값(at_p5) → 등급
    2. 그 밖: 시각마다 등급(격자면 면적 가중) → 6개 시각 평균(mean_0_5) 또는 5시간 뒤 하나(at_p5)
    3. 한 시각이라도 값이 없으면 그 변수는 값 없음(빈 시각을 메우지 않는다, rules와 같음)
    """
    if mode not in MODES:
        raise ValueError(f"mode는 {MODES} 중 하나")
    wc = wc or _WeightCache(geom)
    if key == "precip":  # 1
        hours = RAIN_HOURS if mode == "mean_0_5" else (5,)
        total = _sum_fields([by_hour.get(h) for h in hours])
        g, v = _field_grade(key, total, wc)
        return Summary(g, v, hours, [(hours, v, g)])
    hours = HOURS if mode == "mean_0_5" else (5,)  # 2
    detail = []
    for h in hours:
        g, v = _field_grade(key, by_hour.get(h), wc)
        detail.append((h, v, g))
    if any(g is None for _, _, g in detail):  # 3
        return Summary(None, None, hours, detail)
    gs = [g for _, _, g in detail]
    vs = [v for _, v, _ in detail]
    return Summary(sum(gs) / len(gs), sum(vs) / len(vs), hours, detail)


def summarize_once(key: str, f: Field, geom: BaseGeometry, wc=None) -> Summary:
    """시각이 하나뿐인 값(7일 누적 강수량)."""
    g, v = _field_grade(key, f, wc or _WeightCache(geom))
    return Summary(g, v, (0,), [(0, v, g)])


# ── 연료 수분(1시간 고사연료) ── ELMFIRE 입력 step5_weather.py와 같은 식(m1 격자가 없을 때)


def emc_simard(temp_c: float, rh_pct: float) -> float:
    """평형함수율(%, Simard 1968 · NFDRS). 기온은 ℉로 바꿔 넣는다."""
    tf = temp_c * 9 / 5 + 32
    h = rh_pct
    if h < 10:
        return 0.03229 + 0.281073 * h - 0.000578 * h * tf
    if h < 50:
        return 2.22749 + 0.160107 * h - 0.01478 * tf
    return 21.0606 + 0.005565 * h * h - 0.00035 * h * tf - 0.483199 * h


def dead_fuel_moisture(
    temps: Sequence[float], rhs: Sequence[float], rains: Sequence[float], tau_h: float = 1.0
) -> list[float]:
    """시간별 기온 · 습도 · 강수 → 고사연료 수분(%) 시계열. 1시간 연료는 tau_h = 1.

    1. 평형함수율 E를 1 ~ 35%로 자르고, 시간당 0.5 mm 이상 비가 오면 35%
    2. m(t) = E(t) + (m(t−1) − E(t)) × exp(−1 / τ), 첫 값 = E(0)
    3. 2 ~ 35%로 자른다
    """
    e = [min(max(emc_simard(t, h), 1.0), 35.0) for t, h in zip(temps, rhs, strict=True)]  # 1
    e = [35.0 if r >= 0.5 else x for x, r in zip(e, rains, strict=True)]
    k = math.exp(-1.0 / tau_h)
    out, m = [], e[0]
    for x in e:  # 2
        m = x + (m - x) * k
        out.append(min(max(m, 2.0), 35.0))  # 3
    return out
