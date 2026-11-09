"""지상관측(ASOS) 시간자료 — 기상청 API 허브. 지난 산불을 다시 돌려 볼 때(과거 시연, 명세 AWS_OBS).

지점은 설정으로 고른다(기본 278 = 의성). 9시간 모두 관측값이라 0시 뒤 시각도 이미 지났어야 한다.
풍향은 36방위(10° 단위), 값이 없으면(-9) 앞 시간 값을 쓴다. 강수는 11 ~ 3월에 3시간마다만 있어
없는 시간은 0으로 둔다. 키(KMA_API_KEY)는 주소에만 넣고 오류 · 기록에 남기지 않는다.
"""

import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

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
