# ruff: noqa: E501
"""GPU 서버의 확산 결과(01_spread)를 EC2 file 예측이 그대로 읽는지: ranges_4326.geojson(hour) ·
weather.json(predictions.weather 모양)."""

from datetime import datetime, timedelta

import pytest
from shapely.geometry import box

from app.predict.base import HOURS, PredictionRequest
from app.predict.file import FilePredictor
from app.repositories.predictions import check_slices
from app.weather.base import KST, check_weather, hour_slots
from demo.uiseong import prediction_weather as pw
from demo.uiseong import step1_spread

BASE_AT = datetime(2025, 3, 22, 12, 50)  # 1회차 12:50(한국 시간)
X0, Y0 = 1_098_492.2, 1_819_712.0  # 의성 안평면 괴산리 발화점(EPSG:5179)


def spread_result() -> step1_spread.SpreadResult:
    """P0(관측 화선) ⊆ P1 ⊆ … ⊆ P8, 시간마다 200 m씩 커지는 정사각형."""
    ranges = [
        box(X0 - 300 - 200 * h, Y0 - 300 - 200 * h, X0 + 300 + 200 * h, Y0 + 300 + 200 * h)
        for h in range(HOURS + 1)
    ]
    return step1_spread.SpreadResult(
        calibration={"method": "fixed", "adj": 1.0, "from": "", "parts": []},
        ranges=ranges,
        observed=ranges[0],
        areas_ha=[round(g.area / 1e4, 1) for g in ranges],
        validation=[],
    )


def weather() -> dict:
    slots = hour_slots(BASE_AT.replace(tzinfo=KST))
    winds = [(3.0 + h, 270.0) for h in range(HOURS + 1)]
    asos = {t: {"temp": 22.0, "humidity": 20.0, "precip": 0.0} for t in slots}
    return pw.build_weather(slots, winds, asos, (36.356, 128.689), "ws_gust · wd_obs")


def test_build_weather_is_server_shape():
    w = check_weather(weather())  # 서버 검사(9시간 · 칸 · 범위) 통과
    assert w["source"] == pw.SOURCE
    assert w["hours"][0]["at"] == "2025-03-22T12:00:00+09:00"  # 회차 시각을 내림한 정시
    assert w["hours"][8]["at"] == "2025-03-22T20:00:00+09:00"
    assert {h["kind"] for h in w["hours"]} == {"관측"}
    assert w["hours"][3]["wind_speed"] == 6.0


def test_build_weather_needs_every_asos_hour():
    slots = hour_slots(BASE_AT.replace(tzinfo=KST))
    asos = {t: {"temp": 22.0, "humidity": 20.0, "precip": 0.0} for t in slots[:-1]}
    with pytest.raises(ValueError, match="ASOS"):
        pw.build_weather(slots, [(3.0, 270.0)] * len(slots), asos, (36.3, 128.6), "ws · wd")


def test_build_weather_wraps_360_direction():
    slots = hour_slots(BASE_AT.replace(tzinfo=KST))
    asos = {t: {"temp": 22.0, "humidity": 20.0, "precip": 0.0} for t in slots}
    w = pw.build_weather(slots, [(3.0, 360.0)] * len(slots), asos, (36.3, 128.6), "ws · wd")
    assert {h["wind_dir"] for h in w["hours"]} == {0.0}


def test_read_asos_parses_korean_time(tmp_path):
    path = tmp_path / "weather_hourly.csv"
    path.write_text(
        "time,temp_c,precip_mm,wind_ms,wind_deg,rh_pct\n2025-03-22T12:00,22.2,0.0,3.4,270.0,22.0\n",
        encoding="utf-8",
    )
    rows = pw.read_asos(path)
    assert rows == {
        datetime(2025, 3, 22, 12, tzinfo=KST): {"temp": 22.2, "humidity": 22.0, "precip": 0.0}
    }


def test_namelist_file_ignores_comment():
    text = "&INPUTS\nWS_FILENAME             = 'ws_gust'  ! mph, 10 m\nWD_FILENAME = 'wd_obs'\n"
    assert pw.namelist_file(text, "WS_FILENAME") == "ws_gust"
    assert pw.namelist_file(text, "WD_FILENAME") == "wd_obs"
    assert pw.namelist_file(text, "M1_FILENAME") is None


def test_saved_spread_loads_as_server_file_prediction(tmp_path):
    """01_spread를 EC2 file 예측(기본 crs 4326 + weather 파일)으로 읽으면 서버 저장 검사를 통과한다."""
    r, w = spread_result(), weather()
    step1_spread.save(r, tmp_path, w)
    predictor = FilePredictor(
        path=tmp_path / "ranges_4326.geojson", weather_path=tmp_path / "weather.json"
    )
    start = r.ranges[0]  # EC2의 현재 화선 = 같은 k보 화선
    result = predictor.predict(PredictionRequest(start, BASE_AT, (36.356, 128.689)))
    slices = check_slices(start, result.slices)  # 8개 · 면 · P0 ⊆ P1 ⊆ … ⊆ P8
    assert len(slices) == HOURS
    for got, want in zip(slices, r.ranges[1:], strict=True):
        assert abs(got.area - want.area) / want.area < 1e-6  # 4326 왕복 오차만
    assert result.weather == w  # 예측에 쓴 기상이 그대로 predictions.weather로


def test_save_without_weather_writes_no_weather_file(tmp_path):
    step1_spread.save(spread_result(), tmp_path)
    assert not (tmp_path / "weather.json").exists()
    assert (tmp_path / "ranges_4326.geojson").exists()


def test_slots_follow_band_hours():
    """정시 9개가 ELMFIRE 밴드(1시간 간격) 위에 놓인다 — deck_winds가 그 밴드를 그대로 읽는다."""
    slots = hour_slots(BASE_AT.replace(tzinfo=KST))
    band1 = datetime(2025, 3, 22, 11, 0)
    idx = [(t.replace(tzinfo=None) - band1) / timedelta(hours=1) for t in slots]
    assert idx == [float(i) for i in range(1, HOURS + 2)]
