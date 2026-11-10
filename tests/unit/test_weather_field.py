"""관측소 여러 곳 기상 → ELMFIRE 격자(spread.weather_field · app.weather.aws_minute · field)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from app.weather.aws_minute import AwsMinute, hourly, parse_minutes
from app.weather.base import KST, WeatherUnavailable
from app.weather.field import StationField, load_stations
from spread.units import MPH_PER_MS
from spread.weather_field import Station, gust_factor, interpolate, station_series

T0 = datetime(2025, 3, 22, 12, tzinfo=KST)


def Affine(a, b, c, d, e, f):  # noqa: N802 — rasterio 없이(API 이미지) 쓰는 격자 변환
    return SimpleNamespace(a=a, b=b, c=c, d=d, e=e, f=f)


PROFILE = {"transform": Affine(600, 0, 1_100_000, 0, -600, 1_830_000), "width": 10, "height": 5}


def obs_hours(gust=9.0, mean=5.0, wd=270.0, ta=20.0, hm=25.0, n=3):
    return {
        T0 - timedelta(hours=h): {"gust": gust, "mean": mean, "wd": wd, "ta": ta, "hm": hm, "rn": 0}
        for h in range(n)
    }


def fc_hours(ws=4.0, wd=250.0, ta=18.0, hm=30.0, start=1):
    return {
        T0 + timedelta(hours=h): {"ws": ws, "wd": wd, "ta": ta, "hm": hm, "rn": 0.0}
        for h in range(start, 9)
    }


def test_gust_factor_ratio_and_limits():
    assert gust_factor(obs_hours(9, 5), T0) == pytest.approx(1.8)
    assert gust_factor(obs_hours(30, 5), T0) == 3.0  # 상한
    assert gust_factor({}, T0) == 1.0  # 관측 없음 = 보정 없음
    assert gust_factor(obs_hours(1, 0.2), T0) == 1.0  # 고요한 시간은 뺀다


def test_series_hour0_observed_then_forecast_times_gust_factor():
    s = station_series(obs_hours(9, 5), fc_hours(ws=4), T0)
    assert s.kind == ["관측"] + ["예보"] * 8
    assert s.ws[0] == 9.0 and s.ws[1] == pytest.approx(4 * 1.8)
    assert s.wd[0] == 270.0 and s.wd[3] == 250.0
    assert len(s.m1) == 9 and 2 <= min(s.m1) and max(s.m100) <= 35


def test_series_fills_missing_forecast_hours_by_persistence():
    fc = fc_hours(start=5)  # 1 ~ 4시간 뒤 예보가 없다
    s = station_series(obs_hours(), fc, T0)
    assert s.kind == ["관측"] + ["지속"] * 4 + ["예보"] * 4
    assert s.ws[1:5] == [9.0] * 4


def test_series_without_temperature_keeps_given_moisture():
    obs = {t: {"gust": 8.0, "mean": 5.0, "wd": 270.0} for t in obs_hours()}
    s = station_series(obs, {}, T0, moisture0={"m1": 5.0, "m10": 8.0, "m100": 12.0})
    assert s.m10 == [8.0] * 9
    with pytest.raises(ValueError):
        station_series(obs, {}, T0)


def test_interpolate_is_idw_and_vector_mean_direction():
    st = [Station("a", 1_100_300, 1_829_700), Station("b", 1_105_700, 1_827_300)]
    a = station_series(obs_hours(gust=10, wd=350), fc_hours(ws=5, wd=350), T0)
    b = station_series(obs_hours(gust=10, wd=10), fc_hours(ws=5, wd=10), T0)
    g = interpolate(st, [a, b], PROFILE)
    assert g["ws"].shape == (9, 5, 10) and g["ws"].dtype == np.float32
    assert g["ws"][0, 0, 0] == pytest.approx(10 * MPH_PER_MS, rel=1e-4)
    # 350°와 10°의 평균은 0°(북), 180°가 아니다
    wd = g["wd"][0]
    assert np.all((wd < 15) | (wd > 345))
    assert g["wd"][0, 0, 0] == pytest.approx(350, abs=3)  # 관측소 a 칸은 a 쪽


AWS_TEXT = """#START7777
# YYMMDDHHMI, STN, WD1, WS1, WDS, WSS, WD10, WS10, TA, RE, RN-15m, RN-60m, RN-12H, RN-DAY, HM, PA, PS, TD
202503221200,278,270.0,5.0,270.0,7.0,270.0,4.0,20.1,0,0.0,0.0,0.0,0.0,25.0,1000.0,1010.0,-1.0
202503221201,278,280.0,9.0,280.0,11.0,270.0,4.5,20.2,0,0.0,0.0,0.0,0.0,24.0,1000.0,1010.0,-1.0
202503221202,278,-99.9,-99.9,0,0,0,0,20.3,0,0.0,0.0,0.0,0.0,23.0,1000.0,1010.0,-1.0
202503221300,278,260.0,3.0,260.0,4.0,260.0,3.0,21.0,0,0.0,0.5,0.0,0.0,22.0,1000.0,1010.0,-1.0
202503221200,999,0,1,0,1,0,1,1,0,0,0,0,0,1,1,1,1
#7777END
"""  # noqa: E501


def test_parse_minutes_by_header_names_and_missing():
    rows = parse_minutes(AWS_TEXT)
    assert len(rows) == 5 and rows[0]["stn"] == "278"
    assert rows[0]["at"] == T0 and rows[0]["ws"] == 5.0 and rows[0]["hm"] == 25.0
    assert rows[2]["ws"] is None and rows[2]["wd"] is None  # -99.9 = 결측
    with pytest.raises(WeatherUnavailable, match="열 이름"):
        parse_minutes("202503221200,278,1,2\n")


def test_hourly_gust_mean_and_aws_fetch_filters_station():
    h = hourly([r for r in parse_minutes(AWS_TEXT) if r["stn"] == "278"])
    assert h[T0]["gust"] == 9.0 and h[T0]["mean"] == pytest.approx(7.0)
    assert h[T0]["ta"] == 20.3 and h[T0 + timedelta(hours=1)]["rn"] == 0.5
    urls = []
    aws = AwsMinute("KEY", get_text=lambda u: urls.append(u) or AWS_TEXT)
    got = aws.fetch("278", T0 - timedelta(hours=2), T0 + timedelta(minutes=50))
    assert set(got) == {T0, T0 + timedelta(hours=1)}
    assert "stn=278" in urls[0] and "tm1=202503221000" in urls[0] and "help=1" in urls[0]


def test_station_field_selects_nearby_skips_failures_and_needs_minimum():
    stations = [(s, ll) for s, ll in load_stations() if s.id in {"278", "136", "276", "279"}]
    assert len(stations) == 4
    prof = {"transform": Affine(600, 0, 1_069_200, 0, -600, 1_860_600), "width": 179, "height": 104}

    def observed(stn, start, end):
        if stn == "276":
            raise WeatherUnavailable("청송 자료 없음")
        return obs_hours()

    calls = []

    def forecast(site, base_at):
        calls.append(site)
        return fc_hours()

    f = StationField(observed, forecast, stations=stations, buffer_m=0, min_stations=2)
    assert {s.id for s, _ in f.select(prof)} == {"278", "136", "276"}  # 구미(279)는 범위 밖
    grids, meta = f.build(T0 + timedelta(minutes=50), prof)
    assert grids["ws"].shape == (9, 104, 179)
    assert sorted(meta["stations"]) == ["136", "278"] and "276" in meta["skipped"]
    assert meta["kinds"] == {"관측": 2, "예보": 16}
    with pytest.raises(WeatherUnavailable, match="관측소가 2곳"):
        StationField(observed, forecast, stations=stations, buffer_m=0, min_stations=3).build(
            T0, prof
        )


# 11/10 23:00 실제 키로 부른 응답(의성 278, help=1 · disp=1) 그대로
REAL_AWS = """#START7777
#--------------------------------------------------------------------------------------------------
#  WD1    : 1분 평균 풍향 (degree) : 0-N, 90-E, 180-S, 270-W, 360-무풍
#  WS1    : 1분 평균 풍속 (m/s)
#  WDS    : 최대 순간 풍향 (degree)
#  WSS    : 최대 순간 풍속 (m/s)
#  WD10   : 10분 평균 풍향 (degree)
#  WS10   : 10분 평균 풍속 (m/s)
#  TA     : 1분 평균 기온 (C)
#  RE     : 강수감지 (0-무강수, 1-강수, 음수인 경우 null값)
#  RN-15m : 15분 누적 강수량 (mm)
#  RN-60m : 60분 누적 강수량 (mm)
#  RN-12H : 12시간 누적 강수량 (mm)
#  RN-DAY : 일 누적 강수량 (mm)
#  HM     : 1분 평균 상대습도 (%)
#  PA     : 1분 평균 현지기압 (hPa)
#  PS     : 1분 평균 해면기압 (hPa)
#  TD     : 이슬점온도 (C)
#  *) -50 이하면 관측이 없거나, 에러처리된 것을 표시
#--------------------------------------------------------------------------------------------------
# YYMMDDHHMI   STN    WD1    WS1    WDS    WSS   WD10   WS10      TA      RE RN-15m RN-60m RN-12H RN-DAY      HM      PA      PS      TD
#        KST    ID    deg    m/s    deg    m/s    deg    m/s       C       1     mm     mm     mm     mm       %     hPa     hPa       C
202611102300,278,333.7,0.6,337.7,0.6,340.1,0.3,0.7,-99.9,0.0,0.0,0.0,0.0,92.6,1016.3,1026.6,-0.4,=
#7777END
"""  # noqa: E501


def test_parse_minutes_real_api_response():
    rows = parse_minutes(REAL_AWS)
    assert len(rows) == 1
    r = rows[0]
    assert r["at"] == datetime(2026, 11, 10, 23, tzinfo=KST) and r["stn"] == "278"
    assert (r["wd"], r["ws"], r["ta"], r["hm"], r["rn"]) == (333.7, 0.6, 0.7, 92.6, 0.0)
    h = hourly(rows)[datetime(2026, 11, 10, 23, tzinfo=KST)]
    assert h["gust"] == h["mean"] == 0.6 and h["wd"] == 333.7
