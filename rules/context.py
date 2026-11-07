"""공통 결과(ctx): 규칙 함수를 제안서마다 한 번씩 돌려 모은다.

엔진 묶음(bundles.py)은 이 결과만 받아 쓰고, 공통 규칙 함수를 다시 부르지 않는다.
그래서 S1이 말하는 단계와 S3이 동원 범위에 쓰는 단계가 늘 같다.
지금은 S1이 쓰는 값만 있다. 묶음을 더할 때 필요한 값을 여기에 더한다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from shapely.geometry.base import BaseGeometry

from rules.core.area import range_areas
from rules.core.commander import Commander, decide_commander
from rules.core.facility import FacilityCount, Points, count_facilities
from rules.core.regions import Region, spanned
from rules.core.stage import (
    Change,
    Stage,
    StageJudgment,
    Trend,
    judge_stage,
    stage_change,
    stage_trend,
)
from rules.core.villages import Reached, Village, reached_villages
from rules.core.weather import WindSummary, summarize_wind


@dataclass(frozen=True)
class Inputs:
    """어댑터(app)가 DB에서 모아 넘기는 값."""

    base_at: datetime  # 0시 = 기준 화선 관측 시각
    generated_at: datetime  # 제안서 생성 시각
    reader: str  # 읽는 사람 직함(로그인 사용자, 예: 의성군수)
    prev_stage: Stage | None  # 직전 제안서의 현재 단계(`proposals.judged_stage`), 첫 제안서면 None
    ranges: Sequence[BaseGeometry]  # P0(현재 화선, 없으면 발화점) ~ P8, R-01
    wind_ms: Mapping[int, float]  # 0시 뒤 몇 시간 → 풍속(m/s)
    houses: Points  # 주택 점(GIS건물통합정보 단독 · 공동주택)
    heritage: Points  # 국가유산 점
    sido: Sequence[Region]  # 시 · 도 경계
    sigungu: Sequence[Region]  # 시 · 군 · 구 경계
    villages: Sequence[Village]  # 리(마을) 경계 · 인구. 지금은 의성만 있음


@dataclass(frozen=True)
class Span:
    sido: list[str]
    sigungu: list[str]


@dataclass(frozen=True)
class Context:
    base_at: datetime
    generated_at: datetime
    reader: str
    areas_ha: list[float]  # i번째 = Pi 면적
    wind: WindSummary
    facility: FacilityCount  # P5 안 주택 + 국가유산(시설피해 우려)
    stage_now: StageJudgment
    stage_p5: StageJudgment
    trend: Trend  # P5 단계 vs 현재 단계
    change: Change  # 이번 현재 단계 vs 직전 제안서 현재 단계
    prev_stage: Stage | None
    span_now: Span  # 현재 화선이 걸친 지역
    span_p5: Span
    commander_now: Commander  # 지금 지휘권자(현재 화선 기준)
    commander_next: Commander  # 곧 지휘권자(P5 기준)
    villages: list[Reached]  # P8 안 마을과 처음 닿는 P


def _span(area: BaseGeometry, inputs: Inputs) -> Span:
    return Span(sido=spanned(area, inputs.sido), sigungu=spanned(area, inputs.sigungu))


def build_context(inputs: Inputs) -> Context:
    """규칙 함수를 한 번씩 돌려 공통 결과를 만든다.

    1. P0 ~ P8 면적(R-15)
    2. 현재 풍속 · 0~5시간 평균풍속(R-16)
    3. P5 안 주택 + 국가유산 동수(R-13)
    4. 대응단계(R-17) 두 시점
       - 현재 = 현재 화선 면적 · 현재 풍속 · P5 동수
       - P5 = P5 면적 · 0~5시간 평균풍속 · P5 동수
       - ⚠ 현재 풍속이 관측값 하나라, 잠깐 분 바람으로 현재만 1단계가 될 수 있다(P5는 초기).
         두 시점 모두 0~5시간 평균을 쓰는 안이 있으나 나중에 정한다(11/7)
    5. 추세(P5 vs 현재) · 단계 변화(이번 vs 직전 제안서)
    6. 걸친 지역(R-02): 현재 화선 · P5
    7. 지휘권자(L-01): 지금 = 현재 화선 면적 · 걸친 지역, 곧 = P5 면적 · 걸친 지역
    8. P8 안 마을과 처음 닿는 P(R-02 · R-04). P8이 걸친 시 · 군 · 구에 리 자료가 없으면 오류
       (자료는 나중에 채운다 — 빠진 채로 판정하지 않음, 11/7)
    """
    p0, p5 = inputs.ranges[0], inputs.ranges[5]
    areas = range_areas(inputs.ranges)  # 1
    wind = summarize_wind(inputs.wind_ms)  # 2
    facility = count_facilities(p5, inputs.houses, inputs.heritage)  # 3
    # 4
    stage_now = judge_stage(areas[0], wind.now_ms, facility.total)
    stage_p5 = judge_stage(areas[5], wind.mean_0_5_ms, facility.total)
    # 5
    trend = stage_trend(stage_now.stage, stage_p5.stage)
    change = stage_change(inputs.prev_stage, stage_now.stage)
    # 6
    span_now, span_p5 = _span(p0, inputs), _span(p5, inputs)
    # 7
    commander_now = decide_commander(areas[0], span_now.sido, span_now.sigungu)
    commander_next = decide_commander(areas[5], span_p5.sido, span_p5.sigungu)
    return Context(
        base_at=inputs.base_at,
        generated_at=inputs.generated_at,
        reader=inputs.reader,
        areas_ha=areas,
        wind=wind,
        facility=facility,
        stage_now=stage_now,
        stage_p5=stage_p5,
        trend=trend,
        change=change,
        prev_stage=inputs.prev_stage,
        span_now=span_now,
        span_p5=span_p5,
        commander_now=commander_now,
        commander_next=commander_next,
        villages=_villages(inputs),  # 8
    )


def _villages(inputs: Inputs) -> list[Reached]:
    """P8 안 마을. P8이 걸친 시 · 군 · 구 중 리 자료가 없는 곳이 있으면 오류."""
    have = {v.sigungu for v in inputs.villages}
    missing = [sg for sg in spanned(inputs.ranges[8], inputs.sigungu) if sg not in have]
    if missing:
        raise ValueError(f"리 자료가 없는 시 · 군 · 구: {missing}")
    return reached_villages(inputs.villages, inputs.ranges)
