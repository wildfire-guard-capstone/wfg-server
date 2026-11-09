"""기상청 단기예보 조회서비스(공공데이터포털 VilageFcstInfoService_2.0)로 운영 기상을 받는다.

9시간을 세 API로 채운다(11/9 실제 호출로 확인).
- 이미 지난 정시(정시 자료는 40분쯤 나옴) = 초단기실황(관측)
- 그 뒤 = 초단기예보(매시 30분 발표, 6시간) → 모자라면 단기예보(02 · 05 · … · 23시 발표)
`issued_at` = 쓴 예보의 발표 시각(초단기예보 우선). 하루 넘게 지난 시각은 받을 수 없다(과거는 asos).

키(DATA_GO_KR_API_KEY)는 주소에만 넣고 오류 · 기록에 남기지 않는다.
"""

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.weather.base import KST, Weather, WeatherRequest, WeatherUnavailable, hour, hour_slots

BASE = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
VILLAGE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)  # 단기예보 발표 시각
Items = list[dict[str, Any]]


def grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도 → 기상청 5 km 격자(nx, ny). 기상청 LCC 변환식(격자 5 km, 기준 38N 126E)."""
    re_, grid_km, slat1, slat2, olon, olat, xo, yo = 6371.00877, 5.0, 30, 60, 126, 38, 43, 136
    d = math.pi / 180
    r = re_ / grid_km
    s1, s2, olon, olat = slat1 * d, slat2 * d, olon * d, olat * d
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(
        math.tan(math.pi / 4 + s2 / 2) / math.tan(math.pi / 4 + s1 / 2)
    )
    sf = math.tan(math.pi / 4 + s1 / 2) ** sn * math.cos(s1) / sn
    ro = r * sf / math.tan(math.pi / 4 + olat / 2) ** sn
    ra = r * sf / math.tan(math.pi / 4 + lat * d / 2) ** sn
    theta = (lon * d - olon + math.pi) % (2 * math.pi) - math.pi
    theta *= sn
    return math.floor(ra * math.sin(theta) + xo + 0.5), math.floor(
        ro - ra * math.cos(theta) + yo + 0.5
    )


def precip_mm(value: Any) -> float:
    """강수 값 → mm. 예보는 글로 온다: 「강수없음」 0 · 「1mm 미만」 0.5(가운데) ·
    「30.0~50.0mm」 40 · 「50.0mm 이상」 50 · 「6.0mm」 6."""
    text = str(value).strip()
    if text in ("", "-", "강수없음"):
        return 0.0
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    if not nums:
        raise WeatherUnavailable(f"강수 값을 읽지 못했습니다: {text}")
    if "미만" in text:
        return round(nums[0] / 2, 1)
    if "~" in text and len(nums) >= 2:
        return round((nums[0] + nums[1]) / 2, 1)
    return nums[0]


def _get(op: str, params: dict[str, Any], service_key: str) -> Items:
    key = service_key if "%" in service_key else urllib.parse.quote(service_key, safe="")
    query = urllib.parse.urlencode({"pageNo": 1, "numOfRows": 1000, "dataType": "JSON", **params})
    try:
        with urllib.request.urlopen(f"{BASE}/{op}?serviceKey={key}&{query}", timeout=15) as r:
            raw = r.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as e:  # 주소(키 포함)를 남기지 않는다
        raise WeatherUnavailable(f"기상청 연결 실패({op}): {type(e).__name__}") from None
    if raw.lstrip().startswith("<"):  # 키 · 신청 오류는 XML로 온다
        msg = re.findall(r"<(?:returnAuthMsg|resultMsg|errMsg)>([^<]*)<", raw)
        raise WeatherUnavailable(f"기상청 응답 오류({op}): {msg}")
    body = json.loads(raw)["response"]
    if body["header"]["resultCode"] != "00":
        raise WeatherUnavailable(f"기상청 응답 오류({op}): {body['header']['resultMsg']}")
    return body["body"]["items"]["item"]


def _floor_hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def _by_time(items: Items) -> dict[datetime, dict[str, str]]:
    out: dict[datetime, dict[str, str]] = {}
    for i in items:
        at = datetime.strptime(i["fcstDate"] + i["fcstTime"], "%Y%m%d%H%M").replace(tzinfo=KST)
        out.setdefault(at, {})[i["category"]] = i["fcstValue"]
    return out


def _village_base(now: datetime) -> datetime:
    """가장 최근 단기예보 발표 시각(발표 10분 뒤부터 받음)."""
    t = now - timedelta(minutes=10)
    for back in range(0, 25):
        cand = _floor_hour(t) - timedelta(hours=back)
        if cand.hour in VILLAGE_HOURS:
            return cand
    raise AssertionError("단기예보 발표 시각을 찾지 못함")


@dataclass(frozen=True)
class KmaForecast:
    service_key: str
    name: str = "kma"
    get: Callable[[str, dict[str, Any], str], Items] = _get
    now: Callable[[], datetime] = field(default=lambda: datetime.now(KST))

    def _call(self, op: str, base: datetime, nx: int, ny: int) -> Items:
        params = {"base_date": f"{base:%Y%m%d}", "base_time": f"{base:%H%M}", "nx": nx, "ny": ny}
        return self.get(op, params, self.service_key)

    def fetch(self, request: WeatherRequest) -> Weather:
        """1. 지난 정시 = 초단기실황 2. 나머지 = 초단기예보 → 단기예보 3. 하나라도 비면 실패."""
        slots = hour_slots(request.base_at)
        now = self.now().astimezone(KST)
        if slots[0] < now - timedelta(hours=23):
            raise WeatherUnavailable(
                "기상청 단기예보로는 하루 넘게 지난 시각을 받을 수 없습니다(asos)"
            )
        nx, ny = grid(*request.site)
        hours: dict[datetime, dict[str, Any]] = {}
        last_obs = _floor_hour(now - timedelta(minutes=40))
        for t in (t for t in slots if t <= last_obs):  # 1
            o = {i["category"]: i["obsrValue"] for i in self._call("getUltraSrtNcst", t, nx, ny)}
            hours[t] = hour(t, "관측", o["VEC"], o["WSD"], o["T1H"], o["REH"], precip_mm(o["RN1"]))
        issued = None
        if rest := [t for t in slots if t not in hours]:  # 2
            ultra = _floor_hour(now - timedelta(minutes=45)).replace(minute=30)
            u = _by_time(self._call("getUltraSrtFcst", ultra, nx, ny))
            for t in (t for t in rest if t in u):
                v = u[t]
                hours[t] = hour(
                    t, "예보", v["VEC"], v["WSD"], v["T1H"], v["REH"], precip_mm(v["RN1"])
                )
                issued = ultra
        if rest := [t for t in slots if t not in hours]:
            village = _village_base(now)
            vf = _by_time(self._call("getVilageFcst", village, nx, ny))
            for t in (t for t in rest if t in vf):
                v = vf[t]
                hours[t] = hour(
                    t, "예보", v["VEC"], v["WSD"], v["TMP"], v["REH"], precip_mm(v["PCP"])
                )
                issued = issued or village
        if missing := [t for t in slots if t not in hours]:  # 3
            raise WeatherUnavailable(f"기상청 자료에 없는 시각: {[f'{t:%H:%M}' for t in missing]}")
        return {
            "source": "KMA_FORECAST",
            "issued_at": issued.isoformat() if issued else None,
            "location": {"nx": nx, "ny": ny},
            "hours": [hours[t] for t in slots],
        }
