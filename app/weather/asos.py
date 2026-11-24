"""지상관측(ASOS) 시간자료 — 기상청 API 허브. 지난 산불을 다시 돌려 볼 때(과거 시연, 명세 AWS_OBS).

지점은 설정으로 고른다(기본 278 = 의성). 9시간 모두 관측값이라 0시 뒤 시각도 이미 지났어야 한다.
풍향은 36방위(10° 단위), 값이 없으면(-9) 앞 시간 값을 쓴다. 강수는 11 ~ 3월에 3시간마다만 있어
없는 시간은 0으로 둔다. 키(KMA_API_KEY)는 주소에만 넣고 오류 · 기록에 남기지 않는다.
"""

import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.weather.base import KST, Weather, WeatherRequest, WeatherUnavailable, hour, hour_slots

URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm3.php"
COLS = {"wd": 2, "ws": 3, "ta": 11, "hm": 13, "rn": 15}  # TM STN WD WS … TA TD HM PV RN


def _get_text(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.read().decode("euc-kr", errors="replace")
    except (urllib.error.URLError, TimeoutError) as e:  # 주소(키 포함)를 남기지 않는다
        raise WeatherUnavailable(f"기상청 API 허브 연결 실패: {type(e).__name__}") from None


def parse(text: str) -> dict[datetime, dict[str, float]]:
    """응답 글 → 시각 → {wd, ws, ta, hm, rn}. 없는 값(-9)은 None."""
    out = {}
    for line in text.splitlines():
        cols = line.split()
        if not cols or line.startswith("#") or not cols[0].isdigit():
            continue
        at = datetime.strptime(cols[0], "%Y%m%d%H%M").replace(tzinfo=KST)
        vals = {k: float(cols[i]) for k, i in COLS.items()}
        out[at] = {k: (None if v <= -9 else v) for k, v in vals.items()}
    return out


# 위험도에만 쓰는 관측(예측 입력 아님): 해면기압 PS · 지면온도 TS · 강수 RN의 열
SURFACE_COLS = {"pressure": 8, "rn": 15, "ground_temp": 36}
RAIN_HOURS = 168  # 7일 누적 강수량 = 0시까지 지난 168시간
MIN_ROWS = 160  # 168시간 중 이만큼은 와야 합한다(관측이 끊긴 날을 「비 없음」으로 보지 않게)


def surface(
    auth_key: str,
    base_at: datetime,
    station: str = "278",
    get_text: Callable[[str], str] = _get_text,
) -> dict[str, float | str | None]:
    """0시(base_at을 내린 정시)의 해면기압 · 지면온도와 지난 7일 강수 합 — 호출 한 번.

    강수는 11 ~ 3월에 3시간 강수라 그대로 더하면 된다(4 ~ 10월은 1시간). 값 없는 시각은 0으로 본다
    (비가 없어도 결측으로 온다). 0시 값이 결측이면 None. 줄이 너무 적으면 WeatherUnavailable.
    """
    t0 = base_at.astimezone(KST).replace(minute=0, second=0, microsecond=0)
    start = t0 - timedelta(hours=RAIN_HOURS - 1)
    url = f"{URL}?tm1={start:%Y%m%d%H%M}&tm2={t0:%Y%m%d%H%M}&stn={station}&help=0"
    rows: dict[datetime, list[str]] = {}
    for line in get_text(f"{url}&authKey={auth_key}").splitlines():
        cols = line.split()
        if cols and cols[0].isdigit() and len(cols) > SURFACE_COLS["ground_temp"]:
            rows[datetime.strptime(cols[0], "%Y%m%d%H%M").replace(tzinfo=KST)] = cols
    rows = {t: c for t, c in rows.items() if start <= t <= t0}
    if len(rows) < MIN_ROWS:
        raise WeatherUnavailable(f"지점 {station} 지상관측이 {len(rows)}시간뿐입니다({RAIN_HOURS})")

    def value(t: datetime, key: str, missing: float) -> float | None:
        v = float(rows[t][SURFACE_COLS[key]]) if t in rows else missing
        return None if v <= missing else v

    rain = sum(value(t, "rn", -0.1) or 0.0 for t in rows)
    return {
        "station": station,
        "at": t0.isoformat(),
        "pressure": value(t0, "pressure", 0.0),  # 결측 -9
        "ground_temp": value(t0, "ground_temp", -90.0),  # 결측 -99(영하 9도는 값)
        "rain_7d": round(rain, 1),
    }


@dataclass(frozen=True)
class AsosObserved:
    auth_key: str
    station: str = "278"
    name: str = "asos"
    get_text: Callable[[str], str] = _get_text

    def fetch(self, request: WeatherRequest) -> Weather:
        slots = hour_slots(request.base_at)
        url = (
            f"{URL}?tm1={slots[0]:%Y%m%d%H%M}&tm2={slots[-1]:%Y%m%d%H%M}"
            f"&stn={self.station}&help=0&authKey={self.auth_key}"
        )
        rows = parse(self.get_text(url))
        hours, last = [], {}
        for t in slots:
            row = rows.get(t, {})
            vals = {k: row.get(k) if row.get(k) is not None else last.get(k) for k in COLS}
            if any(vals[k] is None for k in ("wd", "ws", "ta", "hm")):
                raise WeatherUnavailable(
                    f"지점 {self.station}의 {t:%Y-%m-%d %H:%M} 관측값이 없습니다"
                )
            last = vals
            precip = row.get("rn") or 0.0
            hours.append(
                hour(t, "관측", vals["wd"] * 10, vals["ws"], vals["ta"], vals["hm"], precip)
            )
        return {
            "source": "AWS_OBS",
            "issued_at": None,
            "location": {"stn": self.station},
            "hours": hours,
        }
