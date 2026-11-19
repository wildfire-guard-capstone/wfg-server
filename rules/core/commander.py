"""L-01 지휘권자: 피해면적과 불이 걸친 지역으로 정한다(11/7).

대응단계(R-17)와 떼어 판정한다. 근거: 매뉴얼 p.72 단서 ①② · 산림재난방지법 제34조② ·
시행령 제21조① · p.114. 조건이 여러 개 맞으면 더 높은 지휘권자로 정하고, 그 지휘권자를
만든 조건은 모두 이유로 남긴다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum

LARGE_FIRE_HA = 100.0  # 대형산불 → 시·도지사
NATIONAL_HA = 1000.0  # → 산림청장
FOREST_SERVICE_HEAD = "산림청장"


class Level(IntEnum):
    LOCAL = 0  # 관할 시장·군수·구청장
    SIDO = 1  # 시·도지사
    NATIONAL = 2  # 산림청장


class ReasonKind(StrEnum):
    SPAN_SIDO = "span_sido"  # 2개 이상 시·도
    SPAN_SIGUNGU = "span_sigungu"  # 2개 이상 시·군·구
    AREA = "area"


@dataclass(frozen=True)
class Reason:
    kind: ReasonKind
    names: tuple[str, ...] = ()  # 걸친 지역 이름(SPAN_*)
    threshold_ha: float | None = None  # 넘은 면적 기준(AREA)


@dataclass(frozen=True)
class Commander:
    level: Level
    title: str  # 의성군수, 경상북도지사, 산림청장
    reasons: tuple[Reason, ...]  # LOCAL이면 비어 있음


def head_title(region: str) -> str:
    """지역 이름으로 그 지역 장의 직함을 만든다.

    1. 이름 끝 글자를 본다
    2. 군 → 군수, 구 → 구청장, 시 → 시장, 도 → 지사를 붙인다
       (의성군수 · 중구청장 · 안동시장 · 대구광역시장 · 경상북도지사)
    3. 어디에도 안 맞으면 오류
    """
    for suffix, head in (("군", "수"), ("구", "청장"), ("시", "장"), ("도", "지사")):
        if region.endswith(suffix):
            return region + head
    raise ValueError(f"알 수 없는 지역 이름: {region}")


def decide_commander(area_ha: float, sido: Sequence[str], sigungu: Sequence[str]) -> Commander:
    """한 범위의 지휘권자를 정한다: 현재 화선(지금) 또는 P5(곧).

    `sido` · `sigungu`는 그 범위가 걸친 지역(R-02)이며, 보여 줄 순서대로 넘긴다.

    1. 걸친 지역이 하나도 없으면 오류
    2. 네 조건을 하나씩 본다
       - 시·도 2개 이상 → 산림청장
       - 면적 1,000 ha 이상 → 산림청장
       - 시·군·구 2개 이상 → 시·도지사
       - 면적 100 ha 이상(대형산불) → 시·도지사
    3. 맞은 조건 중 가장 높은 지휘권자를 고른다. 하나도 안 맞으면 관할 시장·군수
    4. 그 지휘권자를 만든 조건만 이유로 남긴다(낮은 쪽 조건은 뺌)
    5. 직함을 붙인다: 산림청장 / 그 시·도의 장 / 그 시·군·구의 장
    """
    # 1
    if not sido or not sigungu:
        raise ValueError("범위는 시·도와 시·군·구에 하나 이상 걸쳐야 한다")
    # 2
    conditions = (
        (Level.NATIONAL, len(sido) >= 2, Reason(ReasonKind.SPAN_SIDO, names=tuple(sido))),
        (Level.NATIONAL, area_ha >= NATIONAL_HA, Reason(ReasonKind.AREA, threshold_ha=NATIONAL_HA)),
        (Level.SIDO, len(sigungu) >= 2, Reason(ReasonKind.SPAN_SIGUNGU, names=tuple(sigungu))),
        (Level.SIDO, area_ha >= LARGE_FIRE_HA, Reason(ReasonKind.AREA, threshold_ha=LARGE_FIRE_HA)),
    )
    met = [(level, reason) for level, holds, reason in conditions if holds]
    level = max((lv for lv, _ in met), default=Level.LOCAL)  # 3
    reasons = tuple(r for lv, r in met if lv == level)  # 4
    # 5
    if level is Level.NATIONAL:
        title = FOREST_SERVICE_HEAD
    elif level is Level.SIDO:
        title = head_title(sido[0])
    else:
        title = head_title(sigungu[0])
    return Commander(level, title, reasons)


def handover_steps(by_p: Sequence[Commander]) -> list[tuple[int, Commander]]:
    """구간마다 정한 지휘권자에서 지휘권자가 올라가는 구간을 찾는다(R1, 11/19 평가).

    `by_p[i]` = Pi 범위로 정한 지휘권자(0 = 현재 화선). 결과 = (처음 올라간 구간, 그 지휘권자).
    지휘권자가 바뀌는 시각을 「곧(P5)」 하나로만 넘기면 LLM이 「5시간 뒤 이관」으로 쓴다.

    1. 지금(0번) 지휘권자에서 시작한다
    2. 1번부터 차례로 보며 앞보다 높은 지휘권자가 처음 나오는 구간을 남긴다.
       이유는 그 구간에서 그 지휘권자를 만든 조건이다(면적 · 걸친 지역 중 먼저 맞은 것)
    3. 같거나 낮으면 건너뛴다(범위가 누적이라 내려가지 않음).
       기준을 끝까지 안 넘으면 빈 목록, 두 단계를 넘으면 두 개
    """
    if not by_p:
        raise ValueError("지휘권자가 하나 이상 있어야 한다(0 = 현재 화선)")
    steps = []
    top = by_p[0]  # 1
    for p, c in enumerate(by_p[1:], start=1):  # 2 · 3
        if c.level > top.level:
            steps.append((p, c))
            top = c
    return steps
