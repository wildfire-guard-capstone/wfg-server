"""AWS · ASOS 매분자료(기상청 API 허브 nph-aws2_min) → 관측소의 시간별 돌풍 · 평균풍속 ·
기온 · 습도.

의성 시연과 같은 정의다: 한 시간(H:00 ~ H:59) 안에서
  gust = 가장 센 1분 평균풍속(WS1 최대), mean = 1분 평균풍속의 평균, wd = 1분 풍향의 벡터 평균,
  ta · hm = 그 시간 마지막 값, rn = 그 시간 마지막 1시간 강수(RN-60m).
지금 진행 중인 시간(0시)은 받은 분까지만 쓴다.

응답 열은 help=1 머리말의 열 이름(WS1 · WD1 · TA · HM · RN-60m …)으로 찾는다
(11/10 실제 키 호출로 형식 확인 — tests/unit/test_weather_field.py REAL_AWS).
이름을 못 찾으면 WeatherUnavailable. 키(KMA_API_KEY)는 주소에만 넣고 오류에 남기지 않는다.
"""

import math
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.weather.base import KST, WeatherUnavailable

URL = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-aws2_min"
NAMES = {  # 우리 이름 → 응답 열 이름 후보
    "time": ("YYMMDDHHMI", "YYYYMMDDHHMI", "TM"),
    "stn": ("STN",),
    "wd": ("WD1",),
    "ws": ("WS1",),
    "ta": ("TA",),
    "hm": ("HM",),
    "rn": ("RN-60M", "RN_60M", "RN60M"),
}
MISSING = -50.0  # 이 값 이하는 결측(응답 머리말: "-50 이하면 관측이 없거나, 에러처리된 것")


def _get_text(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.read().decode("euc-kr", errors="replace")
    except (urllib.error.URLError, TimeoutError) as e:  # 주소(키 포함)를 남기지 않는다
        raise WeatherUnavailable(f"기상청 API 허브 연결 실패: {type(e).__name__}") from None


def parse_minutes(text: str) -> list[dict[str, float | datetime | str | None]]:
    """응답 글 → 분 자료 목록. 머리말(# …)에서 열 이름을 찾고, 쉼표 · 공백 구분 모두 읽는다."""
    header: list[str] | None = None
    rows = []
    for line in text.splitlines():
        if line.startswith("#"):
            cols = line.lstrip("#").replace(",", " ").split()
            if "STN" in [c.upper() for c in cols] and any(c.upper() == "WS1" for c in cols):
                header = [c.upper() for c in cols]
            continue
        cols = line.replace(",", " ").split()
        if not cols or not cols[0].isdigit():
            continue
        if header is None:
            raise WeatherUnavailable(
                "AWS 매분자료 머리말에서 열 이름(STN · WS1 …)을 찾지 못했습니다"
            )
        idx = {}
        for key, cands in NAMES.items():
            idx[key] = next((header.index(c) for c in cands if c in header), None)
        if idx["time"] is None:
            idx["time"] = 0
        if any(idx[k] is None for k in ("stn", "ws", "wd")):
            raise WeatherUnavailable(f"AWS 매분자료에 필요한 열이 없습니다: {header}")
        t = cols[idx["time"]]
        fmt = "%Y%m%d%H%M" if len(t) == 12 else "%y%m%d%H%M"
        row: dict[str, float | datetime | str | None] = {
            "at": datetime.strptime(t, fmt).replace(tzinfo=KST),
            "stn": cols[idx["stn"]],
        }
        for key in ("wd", "ws", "ta", "hm", "rn"):
            i = idx[key]
            v = float(cols[i]) if i is not None and i < len(cols) else None
            row[key] = None if v is None or v <= MISSING else v
        rows.append(row)
    return rows


def hourly(minutes: list[dict]) -> dict[datetime, dict[str, float | None]]:
    """분 자료(한 관측소) → 정시 → {gust, mean, wd, ta, hm, rn}."""
    by_hour: dict[datetime, list[dict]] = {}
    for m in minutes:
        by_hour.setdefault(m["at"].replace(minute=0), []).append(m)
    out = {}
    for t, ms in sorted(by_hour.items()):
        ms = sorted(ms, key=lambda m: m["at"])
        ws = [m["ws"] for m in ms if m["ws"] is not None]
        uv = [
            (-m["ws"] * math.sin(math.radians(m["wd"])), -m["ws"] * math.cos(math.radians(m["wd"])))
            for m in ms
            if m["ws"] is not None and m["wd"] is not None
        ]
        wd = None
        if uv:
            u, v = sum(a for a, _ in uv), sum(b for _, b in uv)
            wd = (math.degrees(math.atan2(-u, -v)) + 360) % 360 if (u or v) else ms[-1]["wd"]

        def last(k: str, ms=ms) -> float | None:
            return next((m[k] for m in reversed(ms) if m[k] is not None), None)

        out[t] = {
            "gust": max(ws) if ws else None,
            "mean": sum(ws) / len(ws) if ws else None,
            "wd": wd,
            "ta": last("ta"),
            "hm": last("hm"),
            "rn": last("rn"),
        }
    return out


@dataclass(frozen=True)
class AwsMinute:
    auth_key: str
    get_text: Callable[[str], str] = _get_text

    def fetch(self, stn: str, start: datetime, end: datetime) -> dict[datetime, dict]:
        """한 관측소의 start ~ end 분 자료 → 시간별. 자료가 없으면 빈 dict."""
        url = (
            f"{URL}?tm1={start.astimezone(KST):%Y%m%d%H%M}&tm2={end.astimezone(KST):%Y%m%d%H%M}"
            f"&stn={stn}&disp=1&help=1&authKey={self.auth_key}"
        )
        rows = [r for r in parse_minutes(self.get_text(url)) if r["stn"] == str(stn)]
        return hourly(rows)


def hours_back(t0: datetime, hours: int) -> datetime:
    return t0.astimezone(KST).replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours)
