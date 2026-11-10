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

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

import numpy as np
from shapely.geometry.base import BaseGeometry

from risk import weather as wx
from risk.criteria import AXES, BY_KEY, STATIC_KEYS, VARIABLES, aspect_grade, grade
from risk.fuel import TYPE_NAME
from risk.grid import StaticGrid
from risk.overlap import Weights, grid_weights, weighted_mean, zone_sum

TIMED_KEYS = ("temp", "precip", "wind", "humidity", "pressure", "ground_temp", "moisture")
ASPECT_NAMES = {1: "북", 2: "북동~동", 3: "서~북서·평지", 4: "남동·남서", 5: "남"}


@dataclass
class RiskInputs:
    p5: BaseGeometry  # 5시간 누적 예측 범위(EPSG:5179)
    perimeter: BaseGeometry  # 현재 화선(예측 기준 화선, 발화점이면 점)
    static: StaticGrid | None
    weather: dict[str, dict[int, "wx.Field"]] = field(default_factory=dict)  # 변수 → 시간 → 값
    rain_7d: "wx.Field" = None
    zones: Sequence[tuple[BaseGeometry, float]] = ()  # 취약계층(65세 이상 + 0 ~ 14세) 구역
    stations: Sequence[BaseGeometry] = ()
    mode: str = "mean_0_5"
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
    weight: float = 0.0  # 값이 있는 변수의 가중치 합으로 다시 나눈 값
    contribution: float = 0.0  # weight × grade


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

    def to_dict(self) -> dict:
        d = asdict(self)
        d["score"] = None if self.score is None else round(self.score, 2)
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


def _static_factors(inp: RiskInputs) -> tuple[list[Factor], list[str]]:
    notes: list[str] = []
    sg = inp.static
    out = []
    w = grid_weights(inp.p5, sg.grid) if sg is not None else None
    if w is not None and 0 < w.covered < 0.999:
        notes.append(f"P5의 {w.covered * 100:.1f}%만 지역 격자 안에 있어 그 부분으로 계산")
    for k in STATIC_KEYS:
        var = BY_KEY[k]
        g = v = None
        detail = ""
        if sg is not None and w is not None and w.frac.size:
            g = weighted_mean(sg.grades[k], w)
            if k == "aspect":
                codes = aspect_grade(sg.values["aspect"], np.isnan(sg.values["aspect"]))
                detail = _pct(_shares(codes, w), ASPECT_NAMES)
            elif k == "fuel_type":
                detail = _pct(_shares(sg.values[k], w), TYPE_NAME)
            else:
                v = weighted_mean(sg.values[k], w)
            detail = detail or f"{sg.grid.cs:g} m 격자 {w.frac.size:,}칸 면적 가중"
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


def _infra_factors(inp: RiskInputs) -> list[Factor]:
    out = []
    var = BY_KEY["vulnerable"]
    if inp.zones:
        n, hits = zone_sum(inp.zones, inp.p5)
        g = float(grade("vulnerable", [round(n)])[0])
        detail = f"겹친 구역 {len(hits)}곳, 구역 인원 × 겹친 면적 비율의 합"
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


def compute_risk(inp: RiskInputs) -> RiskResult:
    """1. 변수별 등급 2. 값이 있는 변수의 CSV 가중치 합으로 나눠 R 3. 축별 소계."""
    static, notes = _static_factors(inp)
    by_key = {f.key: f for f in static + _timed_factors(inp) + _infra_factors(inp)}  # 1
    factors = [by_key[v.key] for v in VARIABLES]
    used = [f for f in factors if f.grade is not None]
    wsum = sum(f.weight_csv for f in used)
    score = None
    if used:
        for f in used:  # 2
            f.weight = f.weight_csv / wsum
            f.contribution = f.weight * f.grade
        score = sum(f.contribution for f in used)
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
    )
