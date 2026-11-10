"""위험도 R = Σ(가중치 × 등급) ÷ Σ가중치 — 값이 있는 변수만(위험도-정리.md 「계산식」).

    res = compute_risk(RiskInputs(p5, perimeter, static, weather, rain_7d, zones, stations))
    res.score                 # R(1.00 ~ 5.00), 소수 둘째 자리로 보인다(표시 안 A, 10/5)
    res.factors               # 23개 변수: 값 · 등급 · 가중치(CSV · 다시 나눈 값) · 기여
    res.missing               # 값이 없어 뺀 변수

변수 종류별 처리(「산불 발생 후 위험도 정의」 사안 3):
- 변하지 않는 값 13개: 지역 격자의 칸 등급을 P5 겹침 비율로 가중평균
- 매번 바뀌는 값 8개: weather.summarize(0 ~ 5시간 6개 시각, 칸 등급 → 면적 가중 → 시각 평균)
- 취약계층 수: 구역 인원 × (P5 ∩ 구역) ÷ 구역 면적을 더한 뒤 반올림해 등급(겹친 만큼 합산)
- 소방서와의 거리: 현재 화선 경계에서 가장 가까운 소방서 · 119안전센터 · 119지역대까지
  (안에 있으면 0 km)
"""

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from risk import weather as wx
from risk.criteria import (
    AXES,
    BY_KEY,
    COMBINES,
    DEFAULT_EXPOSURE,
    EXPOSURE_AXES,
    EXPOSURE_BY_KEY,
    HAZARD_AXES,
    HOUSE_RADIUS_M,
    STATIC_KEYS,
    VARIABLES,
    area_grade,
    aspect_grade,
    grade,
    houses_grade,
)
from risk.fuel import TYPE_ID, TYPE_NAME
from risk.grid import StaticGrid
from risk.overlap import Weights, grid_weights, weighted_mean, zone_sum

TIMED_KEYS = ("temp", "precip", "wind", "humidity", "pressure", "ground_temp", "moisture")
# 연료 축의 지역 격자 변수(수분함량 제외). burnable_only면 탈 수 있는 칸만 평균한다(README F-1)
FUEL_STATIC_KEYS = (
    "fuel_type", "fuel_load", "fuel_depth", "stand_age", "canopy_height", "canopy_cover",
)  # fmt: skip
NON_BURNABLE_ID = TYPE_ID["비연소"]
ASPECT_NAMES = {1: "북", 2: "북동~동", 3: "서~북서·평지", 4: "남동·남서", 5: "남"}


@dataclass
class RiskInputs:
    p5: BaseGeometry  # 5시간 누적 예측 범위(EPSG:5179)
    perimeter: BaseGeometry  # 현재 화선(예측 기준 화선, 발화점이면 점)
    static: StaticGrid | None
    weather: dict[str, dict[int, "wx.Field"]] = field(default_factory=dict)  # 변수 → 시간 → 값
    rain_7d: "wx.Field" = None
    # 취약계층(65세 이상 + 0 ~ 14세) 구역: (경계, 인원[, 주택 좌표 (k, 2)]) — overlap.zone_sum
    zones: Sequence[tuple] = ()
    stations: Sequence[BaseGeometry] = ()
    mode: str = "mean_0_5"
    # 결합식: "sqrt_hx" = √(위해 H × 노출 X)(기본, 11/10) · "mean" = 한 번에 가중평균(예전)
    combine: str = "sqrt_hx"
    # 팀 추가 노출 변수 가중치(criteria.EXPOSURE_VARIABLES). {} 이면 AI Hub 23개만(11/10 이전 R)
    exposure: dict = field(default_factory=lambda: dict(DEFAULT_EXPOSURE))
    # 주택 좌표 (k, 2) — 「1 km 안 주택 수」, 없으면 그 변수 값 없음
    houses_xy: np.ndarray | None = None
    # 노출(주택 수 · 취약계층)을 세는 범위 = P5 + 이 거리(m). 0이면 취약계층은 P5 안만(11/10 이전)
    exposure_radius_m: float = HOUSE_RADIUS_M
    # True: 연료 6개는 P5 중 탈 수 있는 칸만 평균(안 타는 땅이 섞여 연료 위험이 묽어지지 않게).
    # False: 예전 방식(안 타는 칸 = 1등급으로 같이 평균) — 비교용
    burnable_only: bool = True
    sources: dict[str, str] = field(default_factory=dict)  # 변수 → 자료 출처(화면용)


@dataclass
class Factor:
    key: str
    name: str
    axis: str
    weight_csv: float
    grade: float | None
    value: float | None
    unit: str
    detail: str = ""
    source: str = ""
    # 값이 있는 변수의 가중치 합으로 다시 나눈 값. sqrt_hx면 자기 묶음(H 또는 X) 안에서 나눈 값
    weight: float = 0.0
    contribution: float = 0.0  # weight × grade(sqrt_hx면 그 묶음 점수에 대한 기여)


@dataclass
class RiskResult:
    score: float | None
    factors: list[Factor]
    missing: list[str]
    used_weight_csv: float
    axes: dict[str, dict]
    p5_ha: float
    mode: str
    notes: list[str] = field(default_factory=list)
    combine: str = "mean"
    hazard: float | None = None  # H: 기상 · 지형 · 연료 가중평균
    exposure: float | None = None  # X: 인프라 · 노출 가중평균

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("score", "hazard", "exposure"):
            d[k] = None if d[k] is None else round(d[k], 2)
        for f in d["factors"]:
            for k in ("grade", "weight", "contribution"):
                if f[k] is not None:
                    f[k] = round(f[k], 3)
            if f["value"] is not None:
                f["value"] = round(f["value"], 3)
        return d


def _shares(codes: np.ndarray, w: Weights) -> dict[int, float]:
    v = codes[w.rows, w.cols]
    out: dict[int, float] = {}
    for c in np.unique(v[~np.isnan(v)]).tolist():
        out[int(c)] = float(w.frac[v == c].sum())
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _pct(shares: dict[int, float], names: dict[int, str]) -> str:
    return " · ".join(f"{names.get(k, k)} {v * 100:.0f}%" for k, v in shares.items() if v >= 0.005)


def burnable_weights(sg: StaticGrid, w: Weights) -> tuple[Weights | None, float | None]:
    """P5 겹침 비율 중 탈 수 있는 칸(fuel_type이 비연소가 아니고 값이 있음)만 남겨 합 1로 다시 나눔.

    돌려주는 것: (탈 수 있는 칸 가중치 또는 None(하나도 없음), P5 중 탈 수 있는 땅 비율).
    연료 값이 없는 칸(NaN)은 탈 수 있는지 몰라 분모에서도 뺀다.
    """
    t = sg.values["fuel_type"][w.rows, w.cols]
    known = ~np.isnan(t)
    burn = known & (t != NON_BURNABLE_ID)
    known_frac = float(w.frac[known].sum())
    if known_frac <= 0:
        return None, None  # 연료 값이 하나도 없음 → 연료 변수는 값 없음(missing)
    share = float(w.frac[burn].sum()) / known_frac
    if not burn.any():
        return None, share
    f = w.frac[burn]
    return Weights(w.rows[burn], w.cols[burn], f / f.sum(), w.covered), share


def _static_factors(inp: RiskInputs) -> tuple[list[Factor], list[str]]:
    notes: list[str] = []
    sg = inp.static
    out = []
    w = grid_weights(inp.p5, sg.grid) if sg is not None else None
    if w is not None and 0 < w.covered < 0.999:
        notes.append(f"P5의 {w.covered * 100:.1f}%만 지역 격자 안에 있어 그 부분으로 계산")
    wb, burn_share = None, None
    if inp.burnable_only and sg is not None and w is not None and w.frac.size:
        wb, burn_share = burnable_weights(sg, w)
        if burn_share is not None and burn_share < 0.999:
            notes.append(
                f"연료 6개는 P5 중 탈 수 있는 땅 {burn_share * 100:.1f}%만으로 평균"
                f"(안 타는 땅 {(1 - burn_share) * 100:.1f}%)"
            )
    for k in STATIC_KEYS:
        var = BY_KEY[k]
        g = v = None
        detail = ""
        if sg is not None and w is not None and w.frac.size:
            fuel_k = inp.burnable_only and k in FUEL_STATIC_KEYS
            wk = wb if (fuel_k and wb is not None) else w
            # P5에 탈 수 있는 칸이 없음 → 번질 연료가 없어 1등급
            if fuel_k and wb is None and burn_share is not None:
                g = 1.0
                detail = "P5에 탈 수 있는 땅이 없음(1등급)"
            else:
                g = weighted_mean(sg.grades[k], wk)
                if k == "aspect":
                    codes = aspect_grade(sg.values["aspect"], np.isnan(sg.values["aspect"]))
                    detail = _pct(_shares(codes, w), ASPECT_NAMES)
                elif k == "fuel_type":
                    detail = _pct(_shares(sg.values[k], w), TYPE_NAME)  # 비연소 비율도 보인다
                else:
                    v = weighted_mean(sg.values[k], wk)
                base = f"{sg.grid.cs:g} m 격자 {wk.frac.size:,}칸 면적 가중"
                if fuel_k:
                    base = f"탈 수 있는 땅만, {base}"
                detail = f"{detail}({base})" if detail and fuel_k else (detail or base)
        src = inp.sources.get(k, "")
        out.append(Factor(k, var.name, var.axis, var.weight, g, v, var.unit, detail, src))
    return out, notes


def _timed_factors(inp: RiskInputs) -> list[Factor]:
    out = []
    wc = wx._WeightCache(inp.p5)
    for k in TIMED_KEYS:
        var = BY_KEY[k]
        s = wx.summarize(k, inp.weather.get(k, {}), inp.p5, inp.mode, wc)
        if k == "precip":
            detail = f"{s.hours[0]}~{s.hours[-1]}시간 뒤 1시간 강수 합"
        elif inp.mode == "mean_0_5":
            detail = "0~5시간 뒤 6개 시각 등급 평균"
        else:
            detail = "5시간 뒤 한 시각"
        src = inp.sources.get(k, "")
        out.append(
            Factor(k, var.name, var.axis, var.weight, s.grade, s.value, var.unit, detail, src)
        )
    s = wx.summarize_once("rain_7d", inp.rain_7d, inp.p5, wc)
    var = BY_KEY["rain_7d"]
    out.append(
        Factor(
            "rain_7d", var.name, var.axis, var.weight, s.grade, s.value, var.unit,
            "0시까지 지난 7일(168시간) 관측 합", inp.sources.get("rain_7d", ""),
        )
    )  # fmt: skip
    return out


def _scope(inp: RiskInputs) -> str:
    r = inp.exposure_radius_m
    return f"P5 + {r / 1000:g} km" if r > 0 else "P5"


def _infra_factors(inp: RiskInputs) -> list[Factor]:
    out = []
    var = BY_KEY["vulnerable"]
    if inp.zones:
        r_m = inp.exposure_radius_m
        n, hits = zone_sum(inp.zones, inp.p5.buffer(r_m) if r_m > 0 else inp.p5)
        g = float(grade("vulnerable", [round(n)])[0])
        by_house = sum(1 for h in hits if h[3] == "houses")
        if by_house:
            detail = (
                f"{_scope(inp)} 겹친 구역 {len(hits)}곳: "
                f"주택 기준 {by_house}곳(구역 인원 × 안에 든 주택 비율) · "
                f"면적 기준 {len(hits) - by_house}곳(주택 자료 없음, 구역 인원 × 겹친 면적 비율)"
            )
        else:
            detail = f"{_scope(inp)} 겹친 구역 {len(hits)}곳, 구역 인원 × 겹친 면적 비율의 합"
        out.append(
            Factor("vulnerable", var.name, "I", var.weight, g, n, var.unit, detail,
                   inp.sources.get("vulnerable", ""))
        )  # fmt: skip
    else:
        out.append(Factor("vulnerable", var.name, "I", var.weight, None, None, var.unit))
    var = BY_KEY["station_dist"]
    if inp.stations:
        d = min(inp.perimeter.distance(s) for s in inp.stations) / 1000.0
        g = float(grade("station_dist", [d])[0])
        out.append(
            Factor("station_dist", var.name, "I", var.weight, g, d, var.unit,
                   "현재 화선 경계에서 가장 가까운 곳(안에 있으면 0)",
                   inp.sources.get("station_dist", ""))
        )  # fmt: skip
    else:
        out.append(Factor("station_dist", var.name, "I", var.weight, None, None, var.unit))
    return out


def _exposure_factors(inp: RiskInputs) -> list[Factor]:
    out = []
    for key, w in inp.exposure.items():
        if not w:
            continue
        var = EXPOSURE_BY_KEY[key]
        if key == "area":
            ha = inp.p5.area / 1e4
            out.append(Factor(key, var.name, var.axis, w, area_grade(ha), ha, var.unit, var.csv,
                              "확산 예측 범위(P5) 면적"))  # fmt: skip
        elif key == "houses_1km":
            if inp.houses_xy is None or not len(inp.houses_xy):
                out.append(Factor(key, var.name, var.axis, w, None, None, var.unit))
                continue
            r_m = inp.exposure_radius_m if inp.exposure_radius_m > 0 else HOUSE_RADIUS_M
            ring = inp.p5.buffer(r_m)
            n = int(shapely.contains_xy(ring, inp.houses_xy[:, 0], inp.houses_xy[:, 1]).sum())
            out.append(Factor(key, var.name, var.axis, w, houses_grade(n), float(n), var.unit,
                              f"P5와 그 바깥 {r_m / 1000:g} km 안 · {var.csv}",
                              inp.sources.get("houses", "")))  # fmt: skip
    return out


def compute_risk(inp: RiskInputs) -> RiskResult:
    """1. 변수별 등급 2. R 3. 축별 소계.

    2. combine="sqrt_hx"(기본): H = 기상 · 지형 · 연료 21개 + 예측 범위 면적 가중평균,
       X = 주택 수 · 취약계층(P5 + 1 km) · 소방서 거리 가중평균, R = √(H × X).
       둘 다 1 ~ 5라 R도 1 ~ 5.
       노출이 낮으면(사람 · 집이 없고 작은 불) 조건이 나빠도 R이 낮다.
       combine="mean": 값이 있는 모든 변수를 가중치 합으로 나눈 가중평균(11/10 이전).
    가중치 = AI Hub CSV 23개 + 팀 추가 노출 변수(inp.exposure, 11/10).
    """
    static, notes = _static_factors(inp)
    by_key = {f.key: f for f in static + _timed_factors(inp) + _infra_factors(inp)}  # 1
    factors = [by_key[v.key] for v in VARIABLES] + _exposure_factors(inp)
    used = [f for f in factors if f.grade is not None]
    wsum = sum(f.weight_csv for f in used)
    if inp.combine not in COMBINES:
        raise ValueError(f"combine은 {COMBINES} 중 하나")
    score = hazard = exposure = None
    if inp.combine == "mean":
        if used:
            for f in used:  # 2
                f.weight = f.weight_csv / wsum
                f.contribution = f.weight * f.grade
            score = sum(f.contribution for f in used)
    else:  # 2. R = √(H × X): 묶음마다 가중평균 → 기하평균
        parts = {}
        for name, axs in (("H", HAZARD_AXES), ("X", EXPOSURE_AXES)):
            fs = [f for f in used if f.axis in axs]
            w = sum(f.weight_csv for f in fs)
            for f in fs:
                f.weight = f.weight_csv / w
                f.contribution = f.weight * f.grade
            parts[name] = sum(f.contribution for f in fs) if fs else None
        hazard, exposure = parts["H"], parts["X"]
        if hazard is not None and exposure is not None:
            score = math.sqrt(hazard * exposure)
        elif hazard is not None:
            score = hazard
            notes.append("노출 변수가 모두 없어 R = 위해 H만으로 계산")
    axes = {}
    for ax, name in AXES.items():  # 3
        fs = [f for f in used if f.axis == ax]
        aw = sum(f.weight_csv for f in fs)
        axes[ax] = {
            "name": name,
            "grade": sum(f.weight_csv * f.grade for f in fs) / aw if aw else None,
            "weight": sum(f.weight for f in fs),
            "contribution": sum(f.contribution for f in fs),
            "n": len(fs),
        }
    return RiskResult(
        score=score,
        factors=factors,
        missing=[f.key for f in factors if f.grade is None],
        used_weight_csv=round(wsum, 4),
        axes=axes,
        p5_ha=inp.p5.area / 1e4,
        mode=inp.mode,
        notes=notes,
        combine=inp.combine,
        hazard=hazard,
        exposure=exposure,
    )
