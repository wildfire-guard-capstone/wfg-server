"""변환 층: 엔진 안의 값을 LLM에 넘길 시각 · 구간 · 숫자로 바꾼다(11/3).

엔진 판정은 P 번호(0 ~ 8)만 쓴다. 시각 · 날짜 · 자정 처리는 이 파일 한 곳에서만 한다.
Pn의 시각은 그 구간의 끝 = 0시(`base_at`) + n시간이다(11/7 「구간 끝 시각을 ~까지로」).
시각은 한국 시간 그대로 받는다(시간대 변환 없음).
"""

from datetime import datetime, timedelta
from enum import StrEnum

MAX_P = 8


def _check_p(p: int) -> None:
    if not 0 <= p <= MAX_P:
        raise ValueError(f"P 번호는 0 ~ {MAX_P}이어야 한다: {p}")


def p_time(base_at: datetime, p: int) -> datetime:
    """Pn 구간의 끝 시각을 낸다.

    1. P 번호가 0 ~ 8이 아니면 오류
    2. 0시 + p시간
    """
    _check_p(p)  # 1
    return base_at + timedelta(hours=p)  # 2


def clock(t: datetime, base_at: datetime) -> str:
    """시각을 LLM에 넘길 글로 쓴다.

    1. 시:분으로 쓴다(14:50)
    2. 0시와 날짜가 다르면(자정을 넘으면) 앞에 「다음 날」을 붙인다(다음 날 01:50)
    3. 이틀 이상 차이 나면 오류(P8까지라 생길 수 없음)
    """
    text = t.strftime("%H:%M")  # 1
    days = (t.date() - base_at.date()).days
    if days == 0:
        return text
    if days == 1:  # 2
        return f"다음 날 {text}"
    raise ValueError(f"0시와 날짜가 {days}일 차이 난다: {t}")  # 3


def p_clock(base_at: datetime, p: int) -> str:
    """Pn 구간의 끝 시각을 글로 낸다: P2 → 「14:50」."""
    return clock(p_time(base_at, p), base_at)


NOW = "지금"  # P0(현재 화선)의 시각


def p_until(base_at: datetime, p: int) -> str:
    """LLM에 넘길 Pn 끝 시각: P0은 「지금」(이미 닿음, 11/7), 그 밖은 0시 + n시간."""
    return NOW if p == 0 else p_clock(base_at, p)


def p_from(base_at: datetime, p: int) -> str | None:
    """Pn 구간의 시작 시각: P0은 None(이미 닿음), 그 밖은 0시 + (n − 1)시간.

    Pn에 처음 닿으면 이 시각 뒤 언제든 닿을 수 있다(구간 끝 시각만 쓰면 시한이 늦어짐).
    """
    return None if p == 0 else p_clock(base_at, p - 1)


WEEKDAYS = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")


def weekday(t: datetime) -> str:
    """요일을 글로 쓴다: 2025-03-22 → 「토요일」."""
    return WEEKDAYS[t.weekday()]


def round1(value: float) -> int | float:
    """숫자를 LLM에 넘길 값으로 줄인다. 판정은 줄이기 전 값으로 한다(11/7).

    1. 소수 한 자리로 반올림한다(48.53 → 48.5)
    2. 소수점 아래가 0이면 정수로 둔다(363.0 → 363)
    """
    rounded = round(value, 1)  # 1
    return int(rounded) if rounded == int(rounded) else rounded  # 2


def num_text(value: float) -> str:
    """숫자를 글로 쓴다: 소수 한 자리 · 천 단위 쉼표(1,200 · 48.5)."""
    return f"{round1(value):,}"


class Zone(StrEnum):
    BURNING = "burning"  # 이미 닿음(P0, 현재 화선)
    P5 = "p5"  # 위험구역(P1 ~ P5)
    P8 = "p8"  # 잠재 위험구역(P6 ~ P8)


def zone_of(p: int) -> Zone:
    """처음 닿는 P 번호를 구간으로 바꾼다(매뉴얼 p.75 5시간 · 8시간).

    1. P 번호가 0 ~ 8이 아니면 오류
    2. 0 → 이미 닿음, 1 ~ 5 → 위험구역, 6 ~ 8 → 잠재 위험구역
    """
    _check_p(p)  # 1
    if p == 0:  # 2
        return Zone.BURNING
    return Zone.P5 if p <= 5 else Zone.P8
