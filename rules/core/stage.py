"""R-17 대응단계: 세 요소 중 가장 높은 요소가 단계를 정한다(매뉴얼 p.13 · p.73).

초기 대응은 모든 요소가 낮을 때만이고, 한 요소라도 상위 기준이면 상위 단계다.
진화예상시간은 MVP에서 뺀다(withheld).
"""

from dataclasses import dataclass
from enum import IntEnum, StrEnum


class Stage(IntEnum):
    INITIAL = 0  # 초기 대응
    STAGE1 = 1  # 확산대응 1단계
    STAGE2 = 2  # 확산대응 2단계


# LLM에 넘길 단계 이름(facts · finding에 씀)
LABEL = {Stage.INITIAL: "초기 대응", Stage.STAGE1: "1단계", Stage.STAGE2: "2단계"}


# 요소별 1단계 · 2단계 하한(p.73). 하한은 「이상」이라 경계값을 포함한다.
AREA_HA = (10.0, 100.0)
WIND_MS = (3.0, 11.0)
FACILITY = (1, 20)  # P5 안 주택 + 국가유산 동수(R-13). 0 = 우려 없음


def _by(value: float, bounds: tuple[float, float]) -> Stage:
    """값 하나를 요소 기준(1단계 하한, 2단계 하한)에 대 본다.

    1. 2단계 하한 이상이면 2단계
    2. 아니고 1단계 하한 이상이면 1단계
    3. 둘 다 아니면 초기 대응
    """
    if value >= bounds[1]:
        return Stage.STAGE2
    if value >= bounds[0]:
        return Stage.STAGE1
    return Stage.INITIAL


def by_area(area_ha: float) -> Stage:
    return _by(area_ha, AREA_HA)


def by_wind(wind_ms: float) -> Stage:
    return _by(wind_ms, WIND_MS)


def by_facility(count: int) -> Stage:
    return _by(count, FACILITY)


@dataclass(frozen=True)
class StageJudgment:
    stage: Stage
    by_area: Stage
    by_wind: Stage | None  # 풍속 값이 없으면 None
    by_facility: Stage
    # `stage`와 같은 단계인 요소("area" · "wind" · "facility"). 초기 대응이면 ()
    decided_by: tuple[str, ...]


def judge_stage(area_ha: float, wind_ms: float | None, facility: int) -> StageJudgment:
    """한 시점을 판정한다: 현재(현재 화선 면적 · 관측 풍속) 또는 P5(P5 면적 · 0~5시간 평균).

    1. 요소마다 단계를 매긴다: 면적(10 · 100 ha) · 풍속(3 · 11 m/s) · 시설(1 · 20동)
    2. 풍속 값이 없으면 풍속 요소는 빼고 본다
    3. 남은 요소 중 가장 높은 단계가 그 시점의 단계다
    4. 그 단계와 같은 요소를 모두 `decided_by`에 담는다. 초기 대응이면 비운다
    """
    # 1
    factors = {
        "area": by_area(area_ha),
        "wind": None if wind_ms is None else by_wind(wind_ms),
        "facility": by_facility(facility),
    }
    known = {name: s for name, s in factors.items() if s is not None}  # 2
    stage = max(known.values())  # 3
    # 4
    decided_by = () if stage is Stage.INITIAL else tuple(n for n, s in known.items() if s == stage)
    return StageJudgment(stage, factors["area"], factors["wind"], factors["facility"], decided_by)


def next_area_threshold(stage: Stage) -> float | None:
    """다음 단계로 올라가는 면적 기준(ha)을 돌려준다.

    1. 초기 대응이면 1단계 하한 10 ha
    2. 1단계면 2단계 하한 100 ha
    3. 2단계면 더 올라갈 단계가 없어 None
    """
    return None if stage is Stage.STAGE2 else AREA_HA[stage]


class Change(StrEnum):
    NEW = "new"  # 그 산불의 첫 제안서
    UP = "up"
    SAME = "same"
    DOWN = "down"  # 예: 화선 정정 뒤


def stage_change(prev: Stage | None, now: Stage) -> Change:
    """직전 제안서의 현재 단계(`proposals.judged_stage`)와 비교한다.

    1. 직전 제안서가 없으면 `new`(첫 제안서)
    2. 이번이 높으면 `up`, 낮으면 `down`
    3. 같으면 `same`
    """
    if prev is None:
        return Change.NEW
    if now > prev:
        return Change.UP
    if now < prev:
        return Change.DOWN
    return Change.SAME


class Trend(StrEnum):
    UP = "up"  # P5 단계가 더 높음
    SAME = "same"
    DOWN = "down"  # P5 단계가 더 낮음(바람이 약해지는 경우 등)


def stage_trend(now: Stage, p5: Stage) -> Trend:
    """P5 단계를 현재 단계와 비교한다.

    1. P5가 높으면 `up`, 낮으면 `down`
    2. 같으면 `same`
    """
    if p5 > now:
        return Trend.UP
    if p5 < now:
        return Trend.DOWN
    return Trend.SAME
