"""기상 제공자: 어떤 원천이든 9시간 · 같은 칸(명세 Weather + kind). 기상청은 가짜 응답으로."""

import json
import urllib.error
from datetime import datetime, timedelta

import pytest

from app.weather import WeatherRequest, WeatherUnavailable, get_weather
from app.weather.asos import AsosObserved, parse
from app.weather.base import KST, check_weather
from app.weather.file import FileWeather
from app.weather.fixed import FixedWeather
from app.weather.kma import KmaForecast, grid, precip_mm

SITE = (36.389893969, 128.580890508)  # 의성 안평면
NOW = datetime(2026, 11, 9, 21, 50, tzinfo=KST)


def at(h: int, m: int = 0, day: int = 9) -> datetime:
    return datetime(2026, 11, day, h, m, tzinfo=KST)


def test_grid_matches_kma_grid():
    assert grid(*SITE) == (88, 102)  # 11/9 실제 호출에 쓴 격자
    assert grid(37.5665, 126.9780) == (60, 127)  # 서울시청


@pytest.mark.parametrize(
    ("text", "mm"),
    [("0", 0.0), ("강수없음", 0.0), ("1mm 미만", 0.5), ("6.0mm", 6.0), ("30.0~50.0mm", 40.0)]
    + [("50.0mm 이상", 50.0), ("2.5", 2.5)],
)
def test_precip_text_to_mm(text, mm):
    assert precip_mm(text) == mm


def fcst(op_times: list[datetime], temp_key: str, rain_key: str, rain: str) -> list[dict]:
    items = []
    for t in op_times:
        values = {"VEC": "300", "WSD": "1.2", temp_key: "5", "REH": "90", rain_key: rain}
        for cat, v in values.items():
            items.append(
                {
                    "fcstDate": f"{t:%Y%m%d}",
                    "fcstTime": f"{t:%H%M}",
                    "category": cat,
                    "fcstValue": v,
                }
            )
    return items


class FakeKma:
    """초단기실황 · 초단기예보(6시간) · 단기예보를 흉내 낸다. 부른 기록을 남긴다."""

    def __init__(self, village_until: int = 5):
        self.calls = []
        self.village_until = village_until

    def __call__(self, op, params, key):
        self.calls.append((op, params["base_date"] + params["base_time"]))
        if op == "getUltraSrtNcst":
            obs = {"VEC": "135", "WSD": "0.1", "T1H": "7.5", "REH": "100", "RN1": "0"}
            return [{"category": k, "obsrValue": v} for k, v in obs.items()]
        if op == "getUltraSrtFcst":
            return fcst([at(22) + timedelta(hours=i) for i in range(6)], "T1H", "RN1", "강수없음")
        times = [at(h, day=10) for h in range(0, self.village_until + 1)] + [at(22), at(23)]
        return fcst(times, "TMP", "PCP", "1mm 미만")


def test_kma_fills_nine_hours_from_three_apis():
    fake = FakeKma()
    w = KmaForecast("KEY", get=fake, now=lambda: NOW).fetch(WeatherRequest(SITE, at(21, 10)))
    assert check_weather(w) is w
    assert w["source"] == "KMA_FORECAST" and w["location"] == {"nx": 88, "ny": 102}
    assert w["issued_at"] == at(21, 30).isoformat()  # 초단기예보 발표 시각
    hours = w["hours"]
    assert [h["at"] for h in hours] == [(at(21) + timedelta(hours=i)).isoformat() for i in range(9)]
    assert [h["kind"] for h in hours] == ["관측"] + ["예보"] * 8
    assert hours[0]["wind_speed"] == 0.1 and hours[0]["temp"] == 7.5  # 실황
    assert hours[8]["precip"] == 0.5  # 05:00 = 단기예보(「1mm 미만」)
    assert fake.calls == [
        ("getUltraSrtNcst", "202611092100"),
        ("getUltraSrtFcst", "202611092130"),
        ("getVilageFcst", "202611092000"),
    ]


def test_kma_fails_when_an_hour_is_missing_or_too_old():
    with pytest.raises(WeatherUnavailable, match="없는 시각"):
        KmaForecast("KEY", get=FakeKma(village_until=4), now=lambda: NOW).fetch(
            WeatherRequest(SITE, at(21, 10))
        )
    with pytest.raises(WeatherUnavailable, match="하루 넘게"):
        KmaForecast("KEY", get=FakeKma(), now=lambda: NOW).fetch(
            WeatherRequest(SITE, NOW - timedelta(days=2))
        )


def test_kma_connection_error_does_not_leak_key(monkeypatch):
    def boom(url, timeout):
        raise urllib.error.URLError(f"refused {url}")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(WeatherUnavailable) as e:
        KmaForecast("SECRET-KEY", now=lambda: NOW).fetch(WeatherRequest(SITE, at(21, 10)))
    assert "SECRET" not in str(e.value) and e.value.__cause__ is None


# 11/9 실제 응답(의성 278, 2025-03-22) 모양:
# TM STN WD WS GST_WD GST_WS GST_TM PA PS PT PR TA TD HM PV RN …
ASOS_TEXT = "\n".join(
    ["#START7777", "# YYMMDDHHMI STN  WD   WS ..."]
    + [
        f"2025032{d}{h:02d}00 278  {wd:>2} {ws:>4}  -9 -9.0   -9 1000.7 1010.2 -9  -9.0"
        f"  {ta:>4}  -0.4  {hm:>4}   5.9   {rn}   -9.0   -9.0   -9.0"
        for d, h, wd, ws, ta, hm, rn in [
            (2, 11, 23, 1.9, 20.2, 25.0, -9.0),
            (2, 12, 27, 3.4, 22.2, 22.0, -9.0),
            (2, 13, 27, 4.7, 24.3, 20.0, 1.5),
            (2, 14, -9, -9.0, 24.7, 18.0, -9.0),  # 바람 결측 → 앞 시간 값
            (2, 15, 27, 7.0, 24.8, 17.0, -9.0),
            (2, 16, 29, 7.4, 24.1, 18.0, -9.0),
            (2, 17, 29, 5.3, 23.3, 18.0, -9.0),
            (2, 18, 29, 4.0, 21.0, 22.0, -9.0),
            (2, 19, 27, 3.0, 18.5, 30.0, -9.0),
        ]
    ]
    + ["#7777END"]
)


def test_asos_parses_observations_and_fills_gaps():
    rows = parse(ASOS_TEXT)
    assert rows[datetime(2025, 3, 22, 14, tzinfo=KST)]["ws"] is None
    urls = []
    provider = AsosObserved("KEY", get_text=lambda url: urls.append(url) or ASOS_TEXT)
    w = provider.fetch(WeatherRequest(SITE, datetime(2025, 3, 22, 11, 24, tzinfo=KST)))
    assert check_weather(w) is w
    assert w["source"] == "AWS_OBS" and w["location"] == {"stn": "278"} and w["issued_at"] is None
    h = w["hours"]
    assert {x["kind"] for x in h} == {"관측"}
    assert h[0]["wind_dir"] == 230 and h[0]["wind_speed"] == 1.9 and h[0]["humidity"] == 25
    assert h[3]["wind_speed"] == 4.7 and h[3]["wind_dir"] == 270  # 결측 → 13시 값
    assert h[2]["precip"] == 1.5 and h[0]["precip"] == 0.0  # 강수 결측 = 0
    assert "tm1=202503221100&tm2=202503221900&stn=278" in urls[0]


def test_asos_fails_for_hours_not_observed():
    provider = AsosObserved("KEY", get_text=lambda url: ASOS_TEXT)
    with pytest.raises(WeatherUnavailable, match="관측값이 없습니다"):
        provider.fetch(WeatherRequest(SITE, datetime(2025, 3, 21, 9, tzinfo=KST)))


def test_fixed_weather_nine_same_hours():
    w = FixedWeather().fetch(WeatherRequest(SITE, at(11, 25)))
    assert check_weather(w) is w and w["source"] == "FIXED"
    assert w["hours"][0]["at"] == at(11).isoformat()
    assert {h["wind_speed"] for h in w["hours"]} == {2.0}  # 3 m/s 아래 = 바람 기준 초기 대응


def test_file_weather_checks_shape(tmp_path):
    good = FixedWeather().fetch(WeatherRequest(SITE, at(11)))
    for h in good["hours"]:
        del h["kind"]
    path = tmp_path / "w.json"
    path.write_text(json.dumps(good), encoding="utf-8")
    w = FileWeather(path).fetch(WeatherRequest(SITE, at(11)))
    assert {h["kind"] for h in w["hours"]} == {"파일"}
    good["hours"] = good["hours"][:8]
    path.write_text(json.dumps(good), encoding="utf-8")
    with pytest.raises(ValueError, match="8시간"):
        FileWeather(path).fetch(WeatherRequest(SITE, at(11)))


def test_get_weather_by_name():
    assert isinstance(get_weather("fixed"), FixedWeather)
    assert isinstance(get_weather("kma", service_key="K"), KmaForecast)
    with pytest.raises(ValueError, match="service_key"):
        get_weather("kma", service_key=None)
    with pytest.raises(ValueError, match="모르는 기상"):
        get_weather("ecmwf")


def test_latest_observed_hour_waits_40_minutes():
    from app.weather.kma import latest_observed_hour

    assert latest_observed_hour(at(21, 39)) == at(20)  # 21시 자료는 아직
    assert latest_observed_hour(at(21, 40)) == at(21)
