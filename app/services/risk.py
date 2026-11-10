"""위험도 R(1.00~5.00): 예측 1회마다 P5 안 23개 변수의 가중평균(위험도-정리.md, 명세 7.6).

⚠ 지금은 가짜 위험도다.
- 변수마다 등급(1~5)을 구하는 `grade_factors()`가 가짜 값을 돌려준다. **진짜 계산은 여기서 한다** —
  P5 폴리곤과 지형 · 연료 격자, 예측 기상, 취약계층 수(villages 추정값 × P5 겹친 비율),
  가장 가까운 소방서 거리로 변수마다 원값 → 등급을 구한다.
- 가중평균 `aggregate()`는 명세 그대로라 진짜 계산으로 바꿔도 그대로 쓴다.
- 저장하지 않고 조회할 때마다 계산한다(저장 위치는 명세 Q-01). 위험도는 제안의 입력이 아니다(11/7).
"""

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Prediction

FAKE_VERSION = "fake-0"

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


@dataclass(frozen=True)
class FactorGrade:
    code: str
    raw_value: float | str | None
    grade: int | None  # 1~5, 못 구하면 None(그 변수는 빼고 가중평균)
    reason: str | None
    source: str | None
    value_at: datetime | None


def grade_factors(session: Session, prediction: Prediction) -> list[FactorGrade]:
    """변수 23개의 원값 · 등급.

    ⚠ 가짜: 예측 id로 시드를 정해 1~5 등급을 만든다(같은 예측이면 늘 같은 값).

    진짜로 바꿀 때 이 함수만 고친다. 못 구한 변수는 grade=None + reason으로 돌려준다.
    """
    rng = random.Random(prediction.id)
    return [
        FactorGrade(code, None, rng.randint(1, 5), "가짜 값(계산 구현 전)", "FAKE", None)
        for code, *_ in FACTORS
    ]


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


def compute_risk(session: Session, prediction: Prediction) -> dict[str, Any]:
    """RISK-01 응답 모양(명세 Risk). 등급 구간(grade)은 아직 정하지 않아 null."""
    result = aggregate(grade_factors(session, prediction))
    return {
        "prediction_id": prediction.id,
        **result,
        "grade": None,
        "method_version": FAKE_VERSION,
        "unavailable_reasons": ["가짜 위험도 — 변수 계산(grade_factors) 구현 전"],
    }
