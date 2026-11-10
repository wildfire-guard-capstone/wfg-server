"""위험도 R(1.00~5.00): 예측 1회마다 P5 안 23개 변수의 가중평균(위험도-정리.md, 명세 7.6).

변수 등급은 `risk/` 패키지(risk.compute, 의성 데모 1b와 같은 계산)가 구하고, 여기서는 DB에서
입력을 모아 넘긴 뒤 명세 모양(Risk · RiskFactor)으로 바꾼다.

- 기온 · 습도 · 풍속 · 강수량: 예측에 쓴 기상(predictions.weather)의 0 ~ 5시간 — 한 점 값
- 취약계층 수: P5와 겹친 리(villages)의 65세 이상 + 0 ~ 14세 추정 × 겹친 면적 비율의 합
- 소방서와의 거리: 예측 기준 화선에서 가장 가까운 소방 시설(facilities 소방, 인접 시군 포함)
- 지형 7 · 연료 6: 지역 격자(설정 RISK_GRID_DIR, risk.grid 형식)가 있을 때만. 요청마다 P5 근처 칸만
  읽는다(격자 전체를 메모리에 두지 않음). 만들기: demo/uiseong/build_risk_grid.py
- 기압 · 지면온도 · 7일 누적 강수량 · 연료 수분함량: 아직 받는 곳이 없다
못 구한 변수는 등급 null + 이유로 보이고, 남은 변수의 가중치로 다시 나눈다(status PARTIAL).
변수 등급은 면적 · 시각 평균이라 소수가 될 수 있다(risk/README.md 사안 3).

`aggregate()`는 명세 그대로다. 저장하지 않고 조회할 때마다 계산한다(저장 위치는 명세 Q-01).
위험도는 제안의 입력이 아니다(11/7).
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from geoalchemy2.shape import to_shape
from shapely import wkb
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import FirePerimeter, Prediction, PredictionSlice
from app.db.models._types import SRID
from app.repositories import base_data
from risk import compute as calc
from risk import grid as grid_mod
from risk.criteria import STATIC_KEYS
from risk.grid import StaticGrid
from risk.weather import HOURS, RAIN_HOURS

log = logging.getLogger(__name__)

METHOD_VERSION = "risk-1"  # 위험도-정리.md · 11/8 결정(0 ~ 5시간 6개 시각 등급 평균)
P5 = 5

# (코드, 원문 변수, 축, CSV 원가중치) — 명세 7.6. 가중치 합 0.94
FACTORS: list[tuple[str, str, str, float]] = [
    ("slope_up", "경사도_up", "terrain", 0.08),
    ("aspect", "사면방향", "terrain", 0.06),
    ("curvature", "곡률", "terrain", 0.04),
    ("terrain_complexity", "지형복잡도", "terrain", 0.03),
    ("elevation", "고도", "terrain", 0.02),
    ("topographic_position", "지형위치지수", "terrain", 0.02),
    ("water_distance", "수계와의 거리", "terrain", 0.02),
    ("fuel_load", "산림밀도/연료량", "fuel", 0.05),
    ("forest_type", "수종종류", "fuel", 0.04),
    ("surface_fuel_depth", "지표면 연료깊이", "fuel", 0.04),
    ("stand_age", "평균수령", "fuel", 0.02),
    ("canopy_height", "수관높이", "fuel", 0.02),
    ("canopy_cover", "수관피복률", "fuel", 0.02),
    ("wind_speed", "풍속", "weather", 0.10),
    ("humidity", "상대습도", "weather", 0.07),
    ("temperature", "기온", "weather", 0.05),
    ("precipitation", "강수량", "weather", 0.04),
    ("ground_temperature", "지면온도", "weather", 0.04),
    ("precipitation_7d", "7일 누적 강수량", "weather", 0.03),
    ("pressure", "기압", "weather", 0.02),
    ("fuel_moisture", "수분함량", "fuel", 0.06),
    ("vulnerable_population", "취약계층 수", "infrastructure", 0.04),
    ("fire_station_distance", "소방서와의 거리", "infrastructure", 0.03),
]
AXES = ("terrain", "fuel", "weather", "infrastructure")

# 명세 코드 → risk/ 변수 키(risk.criteria)
KEY_OF = {
    "slope_up": "slope",
    "aspect": "aspect",
    "curvature": "curvature",
    "terrain_complexity": "complexity",
    "elevation": "elevation",
    "topographic_position": "tpi",
    "water_distance": "water_dist",
    "fuel_load": "fuel_load",
    "forest_type": "fuel_type",
    "surface_fuel_depth": "fuel_depth",
    "stand_age": "stand_age",
    "canopy_height": "canopy_height",
    "canopy_cover": "canopy_cover",
    "wind_speed": "wind",
    "humidity": "humidity",
    "temperature": "temp",
    "precipitation": "precip",
    "ground_temperature": "ground_temp",
    "precipitation_7d": "rain_7d",
    "pressure": "pressure",
    "fuel_moisture": "moisture",
    "vulnerable_population": "vulnerable",
    "fire_station_distance": "station_dist",
}

# 예측 기상(predictions.weather.hours)에서 읽는 값: risk 키 → 시간별 칸 이름
FROM_FORECAST = {"temp": "temp", "humidity": "humidity", "wind": "wind_speed", "precip": "precip"}

NO_GRID = "지형 · 연료 지역 격자 없음(설정 RISK_GRID_DIR)"
OUT_OF_GRID = "P5가 지역 격자 밖이거나 그 칸에 값이 없음"
FIXED_WEATHER = "기상 원천이 고정 시험값(설정 WEATHER=fixed)"
MISSING_REASON = {
    "pressure": "기압을 받는 곳이 아직 없음(예측 기상에 없음)",
    "ground_temp": "지면온도를 받는 곳이 아직 없음(예측 기상에 없음)",
    "rain_7d": "지난 7일 강수 관측을 아직 받지 않음",
    "moisture": "연료 수분함량 자료를 받는 곳이 아직 없음",
    "vulnerable": "P5와 겹친 리에 연령별 추정 인구가 없음",
    "station_dist": "가까운 시군에 소방 시설 자료가 없음",
}
GAP_IN_FORECAST = "예측 기상에 0 ~ 5시간 중 빈 시각이 있음"


@dataclass(frozen=True)
class FactorGrade:
    code: str
    raw_value: float | str | None
    grade: float | None  # 1~5(면적 · 시각 평균이라 소수일 수 있음), 못 구하면 None(빼고 평균)
    reason: str | None
    source: str | None
    value_at: datetime | None


# ── 입력 모으기 ──
def forecast_inputs(weather: dict[str, Any]) -> dict[str, dict[int, float | None]]:
    """예측 기상(0시부터 9개 시각) → risk 입력(변수 → 0시 뒤 몇 시간 → 값).
    강수량은 1 ~ 5시간 뒤 각 시각의 1시간 강수(risk.weather.RAIN_HOURS)."""
    hours = weather.get("hours") or []

    def at(i: int, name: str) -> float | None:
        if i < len(hours) and hours[i].get(name) is not None:
            return float(hours[i][name])
        return None

    out = {k: {h: at(h, name) for h in HOURS} for k, name in FROM_FORECAST.items() if k != "precip"}
    out["precip"] = {h: at(h, "precip") for h in RAIN_HOURS}
    return out


def static_grid(
    bounds: tuple[float, float, float, float],
) -> tuple[StaticGrid | None, str | None]:
    """범위(P5 경계 상자)에 걸친 지역 격자 칸과, 못 읽었을 때 이유.
    설정 RISK_GRID_DIR가 비거나 파일이 없으면 지형 · 연료 13개는 빼고 계산한다."""
    folder = get_settings().risk_grid_dir
    if not folder:
        return None, NO_GRID
    folder = Path(folder)
    if not (folder / "meta.json").exists():
        return None, f"{NO_GRID} — {folder}에 파일이 없음"
    try:
        sg = grid_mod.load(folder, bounds=bounds)
    except Exception as exc:  # noqa: BLE001 — 격자 없이도 나머지 변수로 계산한다
        log.exception("위험도 지역 격자를 읽지 못함: %s", folder)
        return None, f"지역 격자를 읽지 못함({type(exc).__name__})"
    return (sg, None) if sg is not None else (None, OUT_OF_GRID)


def vulnerable_zones(session: Session, area: BaseGeometry) -> list[tuple[BaseGeometry, float]]:
    """범위에 걸친 리 → (경계, 65세 이상 + 0 ~ 14세 추정). 추정이 없는 리(인근 시군)는 뺀다."""
    rows = session.execute(
        text(
            "SELECT ST_AsBinary(geom), pop65_est, pop0_14_est FROM villages"
            f" WHERE {base_data.IN_AREA.format(col='geom')}"
            " AND (pop65_est IS NOT NULL OR pop0_14_est IS NOT NULL) ORDER BY id"
        ),
        {"wkt": area.wkt, "srid": SRID},
    ).all()
    return [(wkb.loads(bytes(g)), float((a or 0) + (b or 0))) for g, a, b in rows]


def build_inputs(session: Session, prediction: Prediction) -> tuple[calc.RiskInputs, str | None]:
    """예측 1회 → (risk 입력, 지역 격자를 못 쓴 이유). 좌표는 DB 그대로(EPSG:5179 = risk 좌표)."""
    p5 = to_shape(
        session.scalar(
            select(PredictionSlice.geom).where(
                PredictionSlice.prediction_id == prediction.id, PredictionSlice.hour == P5
            )
        )
    )
    static, grid_reason = static_grid(p5.bounds)
    perimeter = to_shape(session.get(FirePerimeter, prediction.perimeter_id).geom)
    stations = base_data.facilities(session, perimeter)["fire_stations"]
    nearest = min(stations, key=lambda s: perimeter.distance(s.geom), default=None)
    wsrc = (prediction.weather or {}).get("source") or "미상"
    sources = {k: f"예측 기상({wsrc})" for k in FROM_FORECAST}
    sources["vulnerable"] = "리별 65세 이상 + 0 ~ 14세 추정(villages)"
    if nearest is not None:
        sources["station_dist"] = f"소방 시설(facilities) — {nearest.name}"
    if static is not None:
        sources |= {k: f"지역 격자 {static.meta.get('region') or ''}".strip() for k in STATIC_KEYS}
    inputs = calc.RiskInputs(
        p5=p5,
        perimeter=perimeter,
        static=static,
        weather=forecast_inputs(prediction.weather or {}),
        rain_7d=None,
        zones=vulnerable_zones(session, p5),
        stations=[s.geom for s in stations],
        sources=sources,
    )
    return inputs, grid_reason


# ── 결과 → 명세 모양 ──
def _missing_reason(key: str, grid_reason: str | None) -> str:
    if key in STATIC_KEYS:
        return grid_reason or OUT_OF_GRID
    if key in FROM_FORECAST:
        return GAP_IN_FORECAST
    return MISSING_REASON.get(key, "자료 없음")


def _raw(f: calc.Factor) -> float | str | None:
    if f.value is not None:
        return round(float(f.value), 3)
    return f.detail or None  # 사면방향 · 수종: 범주별 면적 비율 글


def to_grades(
    res: calc.RiskResult,
    *,
    base_at: datetime | None,
    weather_source: str | None,
    grid_reason: str | None,
) -> list[FactorGrade]:
    """risk 결과(변수 키) → 명세 코드 순서의 FactorGrade 23개."""
    by_key = {f.key: f for f in res.factors}
    out = []
    for code, *_ in FACTORS:
        key = KEY_OF[code]
        f = by_key[key]
        if f.grade is None:
            out.append(FactorGrade(code, None, None, _missing_reason(key, grid_reason), None, None))
            continue
        timed = key in FROM_FORECAST
        reason = FIXED_WEATHER if timed and weather_source == "FIXED" else None
        out.append(
            FactorGrade(
                code,
                _raw(f),
                round(float(f.grade), 2),
                reason,
                f.source or None,
                base_at if timed else None,
            )
        )
    return out


def grade_factors(session: Session, prediction: Prediction) -> list[FactorGrade]:
    """변수 23개의 원값 · 등급. 못 구한 변수는 grade=None + reason."""
    inputs, grid_reason = build_inputs(session, prediction)
    res = calc.compute_risk(inputs)
    return to_grades(
        res,
        base_at=prediction.base_at,
        weather_source=(prediction.weather or {}).get("source"),
        grid_reason=grid_reason,
    )


def aggregate(grades: list[FactorGrade]) -> dict[str, Any]:
    """명세 7.6: R = Σ(가중치 × 등급) ÷ Σ(포함 변수의 가중치). 빠진 변수는 분모에서도 뺀다."""
    by_code = {g.code: g for g in grades}
    included = [(f, by_code[f[0]]) for f in FACTORS if by_code[f[0]].grade is not None]
    wsum = sum(f[3] for f, _ in included)
    score = sum(f[3] * g.grade for f, g in included) / wsum if wsum else None
    axes = {}
    for axis in AXES:
        part = [(f[3], g.grade) for f, g in included if f[2] == axis]
        w = sum(x for x, _ in part)
        axes[axis] = round(sum(x * y for x, y in part) / w, 2) if w else None
    factors = []
    for code, name, axis, weight in FACTORS:
        g = by_code[code]
        contribution = weight * g.grade / wsum if (g.grade is not None and wsum) else None
        factors.append(
            {
                "code": code,
                "name": name,
                "axis": axis,
                "raw_value": g.raw_value,
                "grade": g.grade,
                "weight": weight,
                "included": g.grade is not None,
                "contribution": round(contribution, 4) if contribution is not None else None,
                "reason": g.reason,
                "source": g.source,
                "value_at": g.value_at,
            }
        )
    status = (
        "UNAVAILABLE" if not included else "READY" if len(included) == len(FACTORS) else "PARTIAL"
    )
    return {
        "status": status,
        "score": round(score, 2) if score is not None else None,
        "axes": axes,
        "included_weight_sum": round(wsum, 4),
        "factors": factors,
    }


def unavailable_reasons(grades: list[FactorGrade]) -> list[str]:
    """뺀 변수 · 대체값의 이유를 이유별로 묶는다 — 「이유: 변수, 변수 …」."""
    names = {code: name for code, name, *_ in FACTORS}
    groups: dict[str, list[str]] = defaultdict(list)
    for g in grades:
        if g.reason:
            groups[g.reason].append(names[g.code])
    return [f"{reason}: {', '.join(ns)}" for reason, ns in groups.items()]


def compute_risk(session: Session, prediction: Prediction) -> dict[str, Any]:
    """RISK-01 응답 모양(명세 Risk). 등급 구간(grade)은 아직 정하지 않아 null."""
    grades = grade_factors(session, prediction)
    return {
        "prediction_id": prediction.id,
        **aggregate(grades),
        "grade": None,
        "method_version": METHOD_VERSION,
        "unavailable_reasons": unavailable_reasons(grades),
    }
