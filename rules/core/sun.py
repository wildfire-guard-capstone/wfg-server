"""C-05 일출 · 일몰과 J2 밤 판정.

일출 · 일몰은 API 없이 NOAA 태양 위치 근사식으로 계산한다(분 단위, 대기 굴절 0.833°).
시각은 한국 시간(UTC + 9) 그대로 다룬다. 밤 = 그날 일출 전 또는 일몰 뒤(야간산불 정의).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import acos, cos, degrees, pi, radians, sin, tan

KST_MIN = 9 * 60  # UTC → 한국 시간(분)


@dataclass(frozen=True)
class SunTimes:
    sunrise: datetime
    sunset: datetime


def sun_times(day: date, lat: float, lon: float) -> SunTimes:
    """그날의 일출 · 일몰(한국 시간). `lat` · `lon` = 기준점 위도 · 경도(WGS84, 도).

    1. 그날의 태양 적위와 균시차를 근사식으로 구한다
    2. 해가 지평선 아래 0.833°(굴절 · 해 반지름)에 오는 시간각을 구한다
    3. 정오(720분)에서 시간각만큼 앞뒤가 일출 · 일몰(UTC), 9시간을 더한다
    4. 가까운 분으로 반올림한다
    """
    n = day.timetuple().tm_yday
    g = 2 * pi / 365 * (n - 1)  # 1
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * cos(g)
        - 0.032077 * sin(g)
        - 0.014615 * cos(2 * g)
        - 0.040849 * sin(2 * g)
    )
    decl = (
        0.006918
        - 0.399912 * cos(g)
        + 0.070257 * sin(g)
        - 0.006758 * cos(2 * g)
        + 0.000907 * sin(2 * g)
        - 0.002697 * cos(3 * g)
        + 0.00148 * sin(3 * g)
    )
    phi = radians(lat)
    ha = degrees(acos(cos(radians(90.833)) / (cos(phi) * cos(decl)) - tan(phi) * tan(decl)))  # 2
    midnight = datetime(day.year, day.month, day.day)
    rise = 720 - 4 * (lon + ha) - eqtime + KST_MIN  # 3
    sets = 720 - 4 * (lon - ha) - eqtime + KST_MIN
    return SunTimes(
        midnight + timedelta(minutes=round(rise)), midnight + timedelta(minutes=round(sets))
    )  # 4


def is_night(t: datetime, lat: float, lon: float) -> bool:
    """그 시각이 밤인가: 그날 일출 전이거나 일몰 뒤(J2, 매뉴얼 야간산불 정의)."""
    s = sun_times(t.date(), lat, lon)
    return not s.sunrise <= t < s.sunset


def next_events(t: datetime, lat: float, lon: float) -> tuple[datetime, datetime]:
    """t 뒤 처음 오는 일몰 · 일출(오늘 것이 지났으면 다음 날 것)."""
    today, tomorrow = (
        sun_times(t.date(), lat, lon),
        sun_times(t.date() + timedelta(days=1), lat, lon),
    )
    sunset = today.sunset if t < today.sunset else tomorrow.sunset
    sunrise = today.sunrise if t < today.sunrise else tomorrow.sunrise
    return sunset, sunrise
