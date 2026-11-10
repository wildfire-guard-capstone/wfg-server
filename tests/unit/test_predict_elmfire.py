"""ELMFIRE 예측(app.predict.elmfire)과 결과 GeoJSON(app.predict.geojson).

전체 흐름 시험은 진짜 ELMFIRE 대신 작은 가짜 실행 파일을 쓴다: 실행 폴더의 namelist ·
기상 래스터를 읽어 확인하고, 화선에서 동쪽으로 10분에 한 칸씩 번지는 도달시간을 쓴다.
rasterio · scipy가 없으면(API 이미지) 그 시험만 건너뛴다.
"""

import json
import stat
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pyproj import Transformer
from shapely.geometry import Point, box, shape
from shapely.ops import transform

from app.predict import get_predictor
from app.predict.base import HOURS, PredictionRequest
from app.predict.elmfire import ElmfirePredictor, namelist_value, weather_namelist
from app.predict.geojson import slices_geojson
from app.weather.base import WeatherRequest
from app.weather.fixed import FixedWeather
from spread.weather_input import to_elmfire

BASE_AT = datetime.fromisoformat("2025-03-22T12:50:00+09:00")
X0, Y1, CELL, W, H = 1_100_000.0, 1_820_000.0, 30.0, 120, 40  # 3.6 km × 1.2 km
START = box(X0 + 300, Y1 - 690, X0 + 600, Y1 - 510)  # 10 × 6칸


def weather():
    return FixedWeather(wind_ms=4.0).fetch(WeatherRequest((36.36, 128.60), BASE_AT))


def test_namelist_value_and_weather_keys():
    text = "WS_FILENAME             = 'ws_gust'  ! mph\nWD_FILENAME = 'wd_obs'\n"
    assert namelist_value(text, "WS_FILENAME") == "ws_gust"
    with pytest.raises(KeyError):
        namelist_value(text, "M1_FILENAME")
    wx = to_elmfire(weather())
    nl = weather_namelist(wx, wx.t0)
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
    with rasterio.open(deck / "inputs" / "ws_gust.tif", "w", **wxp) as w:
        w.write(np.full((151, 2, 6), 99.0, "float32"))
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


def test_predict_writes_request_weather_and_cuts_hourly_ranges(fake_deck, tmp_path):
    deck, exe = fake_deck
    pred = ElmfirePredictor(deck, exe, tmp_path / "runs", keep_runs=True)
    w = weather()
    result = pred.predict(PredictionRequest(START, BASE_AT, (36.36, 128.60), w))

    seen = json.loads((tmp_path / "runs").glob("pred_*/seen.json").__next__().read_text())
    assert seen["WS_FILENAME"] == "wx_ws" and seen["M1_FILENAME"] == "wx_m1"
    assert seen["ws_bands"] == 9 and seen["ws0"] == pytest.approx(4.0 * 2.23694, abs=1e-3)
    assert seen["HOUR_OF_YEAR"] == "1932" and seen["NUM_IGNITIONS"] == "0"
    assert result.meta["t_start_s"] == 3000.0  # 12:50 − 12:00
    assert result.weather is w and result.source == "elmfire"

    assert len(result.slices) == HOURS
    for small, big in zip([START, *result.slices], result.slices, strict=False):
        assert big.covers(small)
    # 동쪽으로 한 시간에 6칸(180 m)
    assert result.slices[0].bounds[2] == pytest.approx(START.bounds[2] + 180, abs=CELL)
    assert result.slices[1].area > result.slices[0].area
    # 덱 원본은 그대로(실행 폴더에만 썼다)
    assert sorted(p.name for p in (deck / "inputs").iterdir()) == ["adj.tif", "ws_gust.tif"]


def test_predict_removes_run_dir_and_rejects_outside_or_mismatched(fake_deck, tmp_path):
    deck, exe = fake_deck
    pred = ElmfirePredictor(deck, exe, tmp_path / "runs")
    pred.predict(PredictionRequest(Point(X0 + 450, Y1 - 600), BASE_AT, (36.4, 128.6), weather()))
    assert not list((tmp_path / "runs").glob("pred_*"))

    far = box(X0 - 5000, Y1 - 600, X0 - 4000, Y1 - 300)
    with pytest.raises(ValueError, match="덱 범위"):
        pred.predict(PredictionRequest(far, BASE_AT, (36.4, 128.6), weather()))
    late = BASE_AT + timedelta(hours=2)
    with pytest.raises(ValueError, match="맞지 않습니다"):
        pred.predict(PredictionRequest(START, late, (36.4, 128.6), weather()))


class _Field:
    """관측소 기상 격자 대신: 풍속 7 m/s 격자를 주거나(ok) 실패한다."""

    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def build(self, base_at, profile):
        import numpy as np

        from app.weather.base import WeatherUnavailable

        self.calls.append((base_at, profile["width"], profile["height"]))
        if not self.ok:
            raise WeatherUnavailable("관측소 0곳")
        shape = (9, profile["height"], profile["width"])
        grids = {k: np.full(shape, 5.0, "float32") for k in ("wd", "m1", "m10", "m100")}
        grids["ws"] = np.full(shape, 7.0 * 2.23694, "float32")
        return grids, {"stations": ["278"], "kinds": {"관측": 1, "예보": 8}}


def test_predict_uses_station_field_on_weather_grid_or_falls_back(fake_deck, tmp_path):
    deck, exe = fake_deck
    f = _Field()
    pred = ElmfirePredictor(deck, exe, tmp_path / "runs", keep_runs=True, field_provider=f)
    result = pred.predict(PredictionRequest(START, BASE_AT, (36.36, 128.60), weather()))
    assert f.calls == [(BASE_AT, 6, 2)]  # 덱 기상 격자(600 m) 크기로 부른다
    assert result.meta["weather_field"]["mode"] == "stations"
    seen = json.loads(next(Path(result.meta["run_dir"]).glob("seen.json")).read_text())
    assert seen["ws0"] == pytest.approx(7.0 * 2.23694, abs=1e-3)  # 격자 값이 들어갔다

    bad = ElmfirePredictor(deck, exe, tmp_path / "runs2", field_provider=_Field(ok=False))
    result = bad.predict(PredictionRequest(START, BASE_AT, (36.36, 128.60), weather()))
    assert result.meta["weather_field"]["mode"] == "point"
    assert "관측소 0곳" in result.meta["weather_field"]["fallback"]


def test_api_starts_without_spread_package():
    """API 이미지(docker/api)에는 spread/가 없다 — ELMFIRE 코드는 쓸 때만 불러와야 한다."""
    import subprocess

    code = "import sys; sys.modules['spread'] = None; import app.main, app.predict; print('ok')"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr[-800:]
