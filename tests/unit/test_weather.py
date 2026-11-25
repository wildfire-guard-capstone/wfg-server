"""기상 제공자: 어떤 원천이든 9시간 · 같은 칸(명세 Weather + kind). 기상청은 가짜 응답으로."""

import json
import urllib.error
from datetime import datetime, timedelta

import pytest

from app.weather import WeatherRequest, WeatherUnavailable, get_weather
from app.weather.asos import AsosObserved, parse, surface
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


# 11/24 실제 응답(의성 278, 2026-11-24 14시) 모양 — PS 9열 · RN 16열 · TS 37열
SURFACE_ROW = (
    "{t} 278 29 6.7 27 15.0 1330 1008.9 {ps} -9 -9.0 5.3 -8.2 37.0 3.3 {rn} 0.1 0.1 -9.0 -9.0 -9.0"
    " -9.0 -9 -9 - 6 1 43 - -9 -9 -9 5000 0.8 -9.00 -9 {ts} -99.0 -99.0 -99.0"
)
T0 = datetime(2026, 11, 24, 14, tzinfo=KST)


def surface_text(hours=168, last=None, rain=None, delay=0):
    """0시까지 hours시간. last = 마지막 줄의 값, rain = {몇 시간 전: mm},
    delay = 아직 안 올라온 시간 수."""
    out = ["#START7777"]
    for back in range(hours - 1 + delay, delay - 1, -1):
        v = {"ps": 1019.0, "ts": 10.2, "rn": (rain or {}).get(back, -9.0)}
        out.append(
            SURFACE_ROW.format(
                t=f"{T0 - timedelta(hours=back):%Y%m%d%H%M}",
                **(v | (last or {} if back == delay else {})),
            )
        )
    return "\n".join([*out, "#7777END"])


def test_surface_reads_pressure_ground_temp_and_seven_day_rain_in_one_call():
    urls = []
    text = surface_text(rain={2: 1.5, 100: 1.6})
    out = surface("KEY", T0 + timedelta(minutes=5), get_text=lambda u: urls.append(u) or text)
    assert out == {
        "station": "278",
        "at": T0.isoformat(),
        "pressure": 1019.0,
        "ground_temp": 10.2,
        "rain_7d": 3.1,
    }
    assert len(urls) == 1 and "tm1=202611171200&tm2=202611241400&stn=278" in urls[0]


def test_surface_keeps_frost_drops_missing_and_refuses_a_broken_week():
    frost = surface("KEY", T0, get_text=lambda u: surface_text(last={"ts": -9.0}))
    assert frost["ground_temp"] == -9.0 and frost["rain_7d"] == 0.0  # 비 없음은 결측으로 온다
    # 0시 값이 결측이면 바로 앞 관측
    gone = surface("KEY", T0, get_text=lambda u: surface_text(last={"ts": -99.0, "ps": -9.0}))
    assert gone["ground_temp"] == 10.2 and gone["pressure"] == 1019.0
    assert gone["at"] == (T0 - timedelta(hours=1)).isoformat()
    with pytest.raises(WeatherUnavailable, match="100시간뿐"):
        surface("KEY", T0, get_text=lambda u: surface_text(hours=100))


def test_surface_uses_the_latest_hour_when_the_base_hour_is_not_published_yet():
    """11/25: 화선 30분 뒤 예측 — 0시(14시) 줄이 아직 없으면 3시간 안의 가장 최근 관측."""
    late = surface("KEY", T0 + timedelta(minutes=30), get_text=lambda u: surface_text(delay=2))
    assert late["at"] == (T0 - timedelta(hours=2)).isoformat()
    assert late["pressure"] == 1019.0 and late["ground_temp"] == 10.2
    with pytest.raises(WeatherUnavailable, match="아직 안 올라옴"):
        surface("KEY", T0, get_text=lambda u: surface_text(delay=4))


def test_surface_failure_never_stops_the_prediction(monkeypatch, caplog):
    from types import SimpleNamespace

    from app.services import weather as svc

    key = "SECRET-KEY"
    monkeypatch.setattr(
        svc, "get_settings", lambda: SimpleNamespace(kma_api_key=key, weather_station="278")
    )
    for error in (WeatherUnavailable("연결 실패"), RuntimeError(f"http://x?authKey={key}")):

        def boom(*args, error=error):
            raise error

        monkeypatch.setattr(svc.asos, "surface", boom)
        assert svc.surface_from_settings(T0) is None
    assert key not in caplog.text
    monkeypatch.setattr(svc, "get_settings", lambda: SimpleNamespace(kma_api_key=None))
    assert svc.surface_from_settings(T0) is None


def test_surface_is_stored_only_not_in_the_weather_response():
    from app.services.weather import prediction_weather_out

    w = {"source": "KMA_STATIONS", "hours": [], "surface": {"pressure": 1019.0}}
    assert prediction_weather_out(w) == {"source": "KMA_STATIONS", "hours": []}


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


def test_prediction_weather_out_hides_stored_only_fields():
    """PRED-04: DB에는 ELMFIRE 입력 기록(STORED_ONLY)까지 두고 응답에서는 뺀다."""
    from app.services.weather import prediction_weather_out

    stored = {
        "source": "KMA_STATIONS",
        "hours": [
            {
                "at": "2026-11-11T14:00:00+09:00",
                "wind_speed": 3.2,
                "wind_max_1min": 5.3,
                "fuel_moisture_1h": 7.9,
            }
        ],
    }
    out = prediction_weather_out(stored)
    assert out["source"] == "KMA_STATIONS"
    assert out["hours"] == [{"at": "2026-11-11T14:00:00+09:00", "wind_speed": 3.2}]
    assert stored["hours"][0]["wind_max_1min"] == 5.3  # 저장값은 그대로
    assert stored["hours"][0]["fuel_moisture_1h"] == 7.9
