"""ELMFIRE 예측(app.predict.elmfire) · 시연 기상 출처(app.weather.deck) · 결과 GeoJSON.

예측은 기상 입력(app.weather.elmfire_input)만 받는다 — 시험은 그 약속대로 만든 가짜 입력을 넘긴다.
전체 흐름 시험은 진짜 ELMFIRE 대신 작은 가짜 실행 파일을 쓴다: 실행 폴더의 namelist ·
기상 래스터를 읽어 확인하고, 화선에서 동쪽으로 10분에 한 칸씩 번지는 도달시간을 쓴다.
rasterio · scipy가 없으면(API 이미지) 그 시험만 건너뛴다.
"""

import json
import stat
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from pyproj import Transformer
from shapely.geometry import Point, box, shape
from shapely.ops import transform

from app.predict import get_predictor
from app.predict.base import HOURS, PredictionRequest
from app.predict.elmfire import ElmfirePredictor, weather_namelist
from app.predict.geojson import slices_geojson
from app.weather.base import KST, WeatherRequest, WeatherUnavailable
from app.weather.deck import DeckWeather
from app.weather.elmfire_input import (
    ElmfireWeatherInput,
    ElmfireWeatherRequest,
    check_input,
    deck_weather_profile,
    floor_hour,
    namelist_value,
)
from app.weather.fixed import FixedWeather
from spread.units import MPH_PER_MS

BASE_AT = datetime.fromisoformat("2025-03-22T12:50:00+09:00")  # 덱 기상 밴드 2(12:00)
X0, Y1, CELL, W, H = 1_100_000.0, 1_820_000.0, 30.0, 120, 40  # 3.6 km × 1.2 km
START = box(X0 + 300, Y1 - 690, X0 + 600, Y1 - 510)  # 10 × 6칸
_lon, _lat = Transformer.from_crs(5179, 4326, always_xy=True).transform(X0 + 1500, Y1 - 300)
SITE = (_lat, _lon)  # 발화점 — 덱 기상 격자(600 m, 6 × 2칸)의 칸(0, 2)
MS_PER_MPH = 1 / MPH_PER_MS
DECK_FILES = ["adj.tif", "m100_obs.tif", "m10_obs.tif", "m1_obs.tif", "wd_obs.tif", "ws.tif"]
DECK_FILES = sorted([*DECK_FILES, "ws_gust.tif"])


def server_weather(base_at=BASE_AT):
    return FixedWeather(wind_ms=4.0).fetch(WeatherRequest(SITE, base_at))


def fake_input(ws_ms=4.0, base_at=BASE_AT, shape=(9, 2, 6)):
    """약속(ElmfireWeatherInput)대로 만든 가짜 입력 — 예측은 출처를 모른다."""
    values = {"ws": ws_ms * MPH_PER_MS, "wd": 270.0, "m1": 6.0, "m10": 8.0, "m100": 10.0}
    grids = {k: np.full(shape, v, "float32") for k, v in values.items()}
    return ElmfireWeatherInput(
        floor_hour(base_at), grids, server_weather(base_at), {"source": "test"}
    )


def test_namelist_value_and_weather_keys():
    text = "WS_FILENAME             = 'ws_gust'  ! mph\nWD_FILENAME = 'wd_obs'\n"
    assert namelist_value(text, "WS_FILENAME") == "ws_gust"
    with pytest.raises(KeyError):
        namelist_value(text, "M1_FILENAME")
    nl = weather_namelist(floor_hour(BASE_AT))
    assert nl["WS_FILENAME"] == "'wx_ws'" and nl["M100_FILENAME"] == "'wx_m100'"
    assert nl["HOUR_OF_YEAR"] == "1932" and nl["CURRENT_YEAR"] == "2025"  # 12:00


def test_slices_geojson_is_wgs84_multipolygon_with_props():
    slices = [START.buffer(30 * h) for h in range(1, HOURS + 1)]
    fc = slices_geojson(slices, BASE_AT)
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == HOURS
    f1 = fc["features"][0]
    assert f1["geometry"]["type"] == "MultiPolygon"
    lon, lat = f1["geometry"]["coordinates"][0][0][0]
    assert 125 < lon < 131 and 33 < lat < 39  # [경도, 위도]
    assert f1["properties"]["hour"] == 1
    assert f1["properties"]["at"] == (BASE_AT + timedelta(hours=1)).isoformat()
    assert f1["properties"]["area_ha"] == pytest.approx(slices[0].area / 10_000, abs=0.01)
    # 되돌리면 같은 면(소수 7자리 ≈ 1 cm)
    back = transform(
        Transformer.from_crs(4326, 5179, always_xy=True).transform, shape(f1["geometry"])
    )
    assert back.symmetric_difference(slices[0]).area < 1e-4 * slices[0].area


def test_get_predictor_builds_elmfire(tmp_path):
    p = get_predictor("elmfire", deck=str(tmp_path), elmfire="elmfire_1.1", work_dir=str(tmp_path))
    assert isinstance(p, ElmfirePredictor) and p.deck == tmp_path and p.name == "elmfire"


# ── 전체 흐름(가짜 ELMFIRE) ─────────────────────────────────────────────────

FAKE = r'''#!{python}
"""가짜 ELMFIRE: namelist · 기상을 확인하고 화선에서 동쪽으로 10분에 한 칸씩 번진다."""
import json, re, sys
from pathlib import Path
import numpy as np, rasterio

text = Path(sys.argv[1]).read_text()
val = lambda k: re.search(rf"^\s*{{k}}\s*=\s*([^!\n]*)", text, re.M).group(1).strip().strip("'")
t0 = float(val("SIMULATION_TSTART"))
seen = {{k: val(k) for k in ("WS_FILENAME", "M1_FILENAME", "HOUR_OF_YEAR", "NUM_IGNITIONS")}}
with rasterio.open(f"inputs/{{seen['WS_FILENAME']}}.tif") as r:
    seen["ws_bands"] = r.count
    seen["ws0"] = float(r.read(1)[0, 0])
Path("seen.json").write_text(json.dumps(seen))
with rasterio.open("inputs/phi.tif") as r:
    phi, prof = r.read(1), r.profile
burned = phi <= 0
cols = np.arange(phi.shape[1])[None, :].repeat(phi.shape[0], 0)
east = np.where(burned.any(1)[:, None], cols - np.where(burned, cols, -1).max(1)[:, None], -1)
toa = np.where(burned, t0, np.where(east > 0, t0 + east * 600.0, -1.0)).astype("float32")
with rasterio.open("outputs/time_of_arrival_0000001.tif", "w", **prof) as w:
    w.write(toa, 1)
print("ELMFIRE finished successfully")
'''


@pytest.fixture
def fake_deck(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    pytest.importorskip("scipy")
    import numpy as np
    from rasterio.transform import from_origin

    deck = tmp_path / "deck"
    (deck / "inputs").mkdir(parents=True)
    prof = dict(
        driver="GTiff",
        height=H,
        width=W,
        count=1,
        dtype="float32",
        crs="EPSG:5179",
        transform=from_origin(X0, Y1, CELL, CELL),
    )
    with rasterio.open(deck / "inputs" / "adj.tif", "w", **prof) as w:
        w.write(np.ones((H, W), "float32"), 1)
    wxp = {
        **prof,
        "height": 2,
        "width": 6,
        "count": 151,
        "transform": from_origin(X0, Y1, 600, 600),
    }
    bands = np.arange(1, 152, dtype="float32")[:, None, None] * np.ones((1, 2, 6), "float32")
    rasters = {
        "ws_gust": 10.0 + bands,  # 1분 최대(mph): 밴드 b = 10 + b
        "ws": 5.0 + bands,  # 시간평균(mph): 밴드 b = 5 + b
        "wd_obs": np.full((151, 2, 6), 270.0, "float32"),
        "m1_obs": np.full((151, 2, 6), 6.0, "float32"),
        "m10_obs": np.full((151, 2, 6), 8.0, "float32"),
        "m100_obs": np.full((151, 2, 6), 10.0, "float32"),
    }
    for name, a in rasters.items():
        with rasterio.open(deck / "inputs" / f"{name}.tif", "w", **wxp) as w:
            w.write(a.astype("float32"))
    (deck / "elmfire.data").write_text(
        "&INPUTS\nFUELS_AND_TOPOGRAPHY_DIRECTORY = './inputs'\nWEATHER_DIRECTORY = './inputs'\n"
        "WS_FILENAME = 'ws_gust'\nWD_FILENAME = 'wd_obs'\nM1_FILENAME = 'm1_obs'\n"
        "M10_FILENAME = 'm10_obs'\nM100_FILENAME = 'm100_obs'\nDT_METEOROLOGY = 3600.0\n/\n"
        "&OUTPUTS\nOUTPUTS_DIRECTORY = './outputs'\n/\n"
        "&TIME_CONTROL\nSIMULATION_TSTART = 6600.0\nSIMULATION_TSTOP = 35400.0\n"
        "CURRENT_YEAR = 2025\nHOUR_OF_YEAR = 1931\n/\n"
        "&SIMULATOR\nNUM_IGNITIONS = 0\n/\n"
        "&MISCELLANEOUS\nSCRATCH = './scratch'\nMISCELLANEOUS_INPUTS_DIRECTORY = './inputs'\n/\n",
        encoding="utf-8",
    )
    exe = tmp_path / "fake_elmfire"
    exe.write_text(FAKE.format(python=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return deck, str(exe)


def test_predict_writes_input_grids_and_cuts_hourly_ranges(fake_deck, tmp_path):
    deck, exe = fake_deck
    pred = ElmfirePredictor(deck, exe, tmp_path / "runs", keep_runs=True)
    inp = fake_input()
    result = pred.predict(PredictionRequest(START, BASE_AT, SITE, None, inp))

    seen = json.loads((tmp_path / "runs").glob("pred_*/seen.json").__next__().read_text())
    assert seen["WS_FILENAME"] == "wx_ws" and seen["M1_FILENAME"] == "wx_m1"
    assert seen["ws_bands"] == 9 and seen["ws0"] == pytest.approx(4.0 * MPH_PER_MS, abs=1e-3)
    assert seen["HOUR_OF_YEAR"] == "1932" and seen["NUM_IGNITIONS"] == "0"
    assert result.meta["t_start_s"] == 3000.0  # 12:50 − 12:00
    assert result.meta["weather_input"] == {"source": "test"}
    assert result.weather is inp.weather and result.source == "elmfire"

    assert len(result.slices) == HOURS
    for small, big in zip([START, *result.slices], result.slices, strict=False):
        assert big.covers(small)
    # 동쪽으로 한 시간에 6칸(180 m)
    assert result.slices[0].bounds[2] == pytest.approx(START.bounds[2] + 180, abs=CELL)
    assert result.slices[1].area > result.slices[0].area
    # 덱 원본은 그대로(실행 폴더에만 썼다)
    assert sorted(p.name for p in (deck / "inputs").iterdir()) == DECK_FILES


def test_predict_needs_input_and_rejects_outside_mismatched_or_bad_grid(fake_deck, tmp_path):
    deck, exe = fake_deck
    pred = ElmfirePredictor(deck, exe, tmp_path / "runs")
    pred.predict(PredictionRequest(Point(X0 + 450, Y1 - 600), BASE_AT, SITE, None, fake_input()))
    assert not list((tmp_path / "runs").glob("pred_*"))

    with pytest.raises(ValueError, match="기상 입력이 없습니다"):
        pred.predict(PredictionRequest(START, BASE_AT, SITE))
    far = box(X0 - 5000, Y1 - 600, X0 - 4000, Y1 - 300)
    with pytest.raises(ValueError, match="덱 범위"):
        pred.predict(PredictionRequest(far, BASE_AT, SITE, None, fake_input()))
    late = BASE_AT + timedelta(hours=2)
    with pytest.raises(ValueError, match="맞지 않습니다"):
        pred.predict(PredictionRequest(START, late, SITE, None, fake_input()))
    with pytest.raises(ValueError, match="격자가"):
        pred.predict(PredictionRequest(START, BASE_AT, SITE, None, fake_input(shape=(9, 3, 6))))


def test_deck_weather_cuts_nine_bands_and_site_hours(fake_deck, tmp_path):
    """시연 출처: 덱 기상에서 화선 시각부터 9밴드, 발화점 칸 바람 + 서버 기상의 기온 · 습도."""
    deck, exe = fake_deck
    profile = deck_weather_profile(deck)
    server = server_weather()
    inp = DeckWeather(deck).build(ElmfireWeatherRequest(BASE_AT, SITE, profile, server))
    check_input(inp, profile)
    assert inp.t0 == datetime(2025, 3, 22, 12, tzinfo=KST)  # 덱 시작 11:00 → 밴드 2부터
    assert inp.meta["bands"] == [2, 10] and inp.meta["temp_humidity_from"] == "FIXED"
    assert inp.grids["ws"][:, 0, 0].tolist() == [10.0 + b for b in range(2, 11)]
    # 0시 1시간 앞 밴드(덱 밴드 1 = 11:00)를 같이 자른다 — 시연과 같은 ELMFIRE 계산
    assert inp.meta["lead_band"] == 1 and float(inp.lead["ws"][0, 0]) == 11.0
    w, h0, h8 = inp.weather, inp.weather["hours"][0], inp.weather["hours"][8]
    assert w["source"] == "AWS_OBS" and {h["kind"] for h in w["hours"]} == {"관측"}
    assert h0["wind_max_1min"] == pytest.approx(12.0 * MS_PER_MPH, abs=0.01)  # 밴드 2 = 12 mph
    assert h0["wind_speed"] == pytest.approx(7.0 * MS_PER_MPH, abs=0.01)  # ws.tif 밴드 2 = 7 mph
    assert h8["wind_max_1min"] == pytest.approx(20.0 * MS_PER_MPH, abs=0.01)
    assert h0["wind_dir"] == 270.0 and h0["temp"] == server["hours"][0]["temp"]

    # 이 입력으로 돌리면 덱 밴드 1(11:00) ~ 10이 ELMFIRE 밴드 1 ~ 10으로 들어가고 시작 시각도
    # 덱과 같다(12:50 = 밴드 1 뒤 6600초) — 덱을 그대로 돌린 시연과 같은 계산. 결과 기상 = 9시간
    pred = ElmfirePredictor(deck, exe, tmp_path / "runs", keep_runs=True)
    result = pred.predict(PredictionRequest(START, BASE_AT, SITE, None, inp))
    seen = json.loads(next(Path(result.meta["run_dir"]).glob("seen.json")).read_text())
    assert seen["ws0"] == 11.0 and seen["ws_bands"] == 10 and seen["HOUR_OF_YEAR"] == "1931"
    assert result.meta["t_start_s"] == 6600.0 and result.meta["lead_band"] is True
    assert result.weather is w and result.meta["weather_input"]["source"] == "deck"


def test_deck_weather_outside_time_or_without_server_weather(fake_deck):
    """덱에 없는 시각 · 서버 기상 없음은 WeatherUnavailable(요청 503) — 다른 기상으로 대신 안 함."""
    deck, _ = fake_deck
    profile = deck_weather_profile(deck)
    src = DeckWeather(deck)
    early = datetime(2025, 3, 22, 10, 30, tzinfo=KST)  # 덱은 11:00부터
    late = datetime(2025, 3, 28, 10, 0, tzinfo=KST)  # 끝(3/28 17:00)까지 9시간이 안 됨
    for at in (early, late):
        with pytest.raises(WeatherUnavailable, match="시연 기상"):
            src.build(ElmfireWeatherRequest(at, SITE, profile, server_weather(at)))
    with pytest.raises(WeatherUnavailable, match="서버 기상"):
        src.build(ElmfireWeatherRequest(BASE_AT, SITE, profile, None))


def test_weather_sources_are_stations_and_deck_only():
    from app.services.prediction import elmfire_source_from_settings

    with pytest.raises(ValueError, match="모르는 기상 출처"):
        elmfire_source_from_settings("point")


def test_api_starts_without_spread_package():
    """API 이미지(docker/api)에는 spread/가 없다 — ELMFIRE 코드는 쓸 때만 불러와야 한다."""
    import subprocess

    code = "import sys; sys.modules['spread'] = None; import app.main, app.predict; print('ok')"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr[-800:]


def test_deck_weather_at_deck_start_has_no_lead_band(fake_deck, tmp_path):
    """덱 첫 시각(11:00)의 화선은 앞 밴드가 없다 — 덱을 그대로 돌린 시연도 밴드 1부터라 같다."""
    deck, exe = fake_deck
    profile = deck_weather_profile(deck)
    at = datetime(2025, 3, 22, 11, 24, tzinfo=KST)  # 의성 발화(괴산리)
    inp = DeckWeather(deck).build(ElmfireWeatherRequest(at, SITE, profile, server_weather(at)))
    assert inp.lead is None and inp.meta["lead_band"] is None and inp.meta["bands"] == [1, 9]
    pred = ElmfirePredictor(deck, exe, tmp_path / "runs", keep_runs=True)
    result = pred.predict(PredictionRequest(START, at, SITE, None, inp))
    seen = json.loads(next(Path(result.meta["run_dir"]).glob("seen.json")).read_text())
    assert seen["ws_bands"] == 9 and seen["ws0"] == 11.0 and result.meta["t_start_s"] == 1440.0


def test_check_input_rejects_bad_lead_band():
    inp = fake_input()
    bad = ElmfireWeatherInput(
        inp.t0, inp.grids, inp.weather, inp.meta, {k: a[0][:1] for k, a in inp.grids.items()}
    )
    profile = {"height": 2, "width": 6}
    with pytest.raises(ValueError, match="앞 밴드"):
        check_input(bad, profile)
    ok = ElmfireWeatherInput(
        inp.t0, inp.grids, inp.weather, inp.meta, {k: a[0] for k, a in inp.grids.items()}
    )
    check_input(ok, profile)
