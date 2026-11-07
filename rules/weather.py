"""R-16 기상 요약: 시간별 기상에서 판정에 쓰는 값을 뽑는다.

시간별 기상은 0시(`base_at`)부터 0 ~ 8시간 뒤 9개다(`predictions.weather.hours`).
지금은 S1(대응단계)이 쓰는 풍속만 뽑는다. 최대 풍속 · 풍향은 S4를 만들 때 더한다.
"""

from collections.abc import Mapping
from dataclasses import dataclass

MEAN_HOURS = range(0, 6)  # 0~5시간 평균에 넣는 시간: 0 · 1 · 2 · 3 · 4 · 5시간 뒤


@dataclass(frozen=True)
class WindSummary:
    now_ms: float  # 0시간(현재 관측) 풍속
    mean_0_5_ms: float  # 0~5시간 평균풍속(P5 대응단계에 씀)


def summarize_wind(wind_ms: Mapping[int, float]) -> WindSummary:
    """시간별 풍속(0시 뒤 몇 시간 → m/s)으로 현재 풍속과 0~5시간 평균풍속을 낸다.

    1. 0 ~ 5시간 값이 하나라도 없으면 오류(빠진 값을 메우지 않는다, 11/7)
    2. 현재 풍속 = 0시간 값
    3. 0~5시간 평균 = 6개 값의 산술평균
    """
    # 1
    missing = [h for h in MEAN_HOURS if wind_ms.get(h) is None]
    if missing:
        raise ValueError(f"풍속 값이 없는 시간: {missing}")
    values = [wind_ms[h] for h in MEAN_HOURS]
    return WindSummary(now_ms=wind_ms[0], mean_0_5_ms=sum(values) / len(values))  # 2 · 3
