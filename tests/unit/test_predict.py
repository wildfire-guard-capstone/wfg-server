import json
from datetime import datetime

import pytest
from pyproj import Transformer
from shapely.geometry import Point, box, mapping
from shapely.ops import transform

from app.predict import get_predictor
from app.predict.base import HOURS, PredictionRequest, fixed_weather
from app.predict.file import FilePredictor
from app.predict.synthetic import SyntheticPredictor
from app.weather import WeatherRequest
from app.weather.fixed import FixedWeather

BASE_AT = datetime(2025, 3, 22, 11, 25)
# 의성 안평면 근처(EPSG:5179)
START = Point(1_100_000, 1_800_000)


def request(start=START, weather=None) -> PredictionRequest:
    return PredictionRequest(start=start, base_at=BASE_AT, site=(36.4, 128.6), weather=weather)


def test_fixed_weather_starts_at_floored_hour_and_has_nine_hours():
    w = fixed_weather(BASE_AT, 4.0, 270, "TEST")
    assert w["source"] == "TEST"
    assert len(w["hours"]) == HOURS + 1
    assert w["hours"][0]["at"] == "2025-03-22T11:00:00"
    assert w["hours"][8]["at"] == "2025-03-22T19:00:00"
    assert {h["wind_speed"] for h in w["hours"]} == {4.0}


def test_synthetic_slices_are_nested_and_grow_downwind():
    result = SyntheticPredictor().predict(request())
    assert len(result.slices) == HOURS
    assert result.slices[0].covers(START)
    for small, big in zip(result.slices, result.slices[1:], strict=False):
        assert big.covers(small)
        assert big.area > small.area
    # 서풍(270)이면 동쪽으로 더 퍼진다
    minx, _, maxx, _ = result.slices[-1].bounds
    assert maxx - START.x > START.x - minx
    assert result.weather["source"] == "SYNTHETIC"


def test_synthetic_uses_given_weather():
    """서버가 받은 기상을 넘기면 그 풍향으로 퍼지고, 그 기상을 그대로 돌려준다."""
    weather = FixedWeather(wind_deg=90.0).fetch(WeatherRequest((36.36, 128.60), BASE_AT))
    result = SyntheticPredictor().predict(request(weather=weather))
    minx, _, maxx, _ = result.slices[-1].bounds
    assert START.x - minx > maxx - START.x  # 동풍(90) → 서쪽으로 더 퍼짐
    assert result.weather is weather


def test_file_prefers_its_own_weather_file(tmp_path):
    path, wpath = tmp_path / "p.geojson", tmp_path / "w.json"
    path.write_text(json.dumps(feature_collection(squares())), encoding="utf-8")
    wpath.write_text(json.dumps({"source": "ELMFIRE_RUN", "hours": []}), encoding="utf-8")
    given = FixedWeather().fetch(WeatherRequest((36.36, 128.60), BASE_AT))
    assert FilePredictor(path, crs=5179).predict(request(weather=given)).weather is given
    own = FilePredictor(path, crs=5179, weather_path=wpath).predict(request(weather=given))
    assert own.weather["source"] == "ELMFIRE_RUN"  # 범위를 만들 때 쓴 기상이 우선


def test_synthetic_covers_polygon_start():
    start = START.buffer(300)
    result = SyntheticPredictor().predict(request(start))
    assert result.slices[0].covers(start)


def feature_collection(slices, crs_from=None):
    to = Transformer.from_crs(5179, crs_from, always_xy=True).transform if crs_from else None
    features = [
        {
            "type": "Feature",
            "properties": {"hour": h},
            "geometry": mapping(transform(to, g) if to else g),
        }
        for h, g in enumerate(slices, start=1)
    ]
    return {"type": "FeatureCollection", "features": features}


def squares():
    return [
        box(START.x - 100 * h, START.y - 100 * h, START.x + 100 * h, START.y + 100 * h)
        for h in range(1, 9)
    ]


def test_file_reads_5179_as_is(tmp_path):
    path = tmp_path / "p.geojson"
    path.write_text(json.dumps(feature_collection(squares())), encoding="utf-8")
    result = FilePredictor(path, crs=5179).predict(request())
    assert [round(g.area) for g in result.slices] == [round(g.area) for g in squares()]
    assert result.weather["source"] == "FILE_FIXED"
    assert result.meta["path"] == str(path)


def test_file_transforms_4326_to_5179(tmp_path):
    path = tmp_path / "p.geojson"
    path.write_text(json.dumps(feature_collection(squares(), crs_from=4326)), encoding="utf-8")
    result = get_predictor("file", path=str(path)).predict(request())
    assert result.slices[7].centroid.distance(START) < 1.0
    assert result.slices[7].area == pytest.approx(squares()[7].area, rel=1e-3)


def test_file_uses_weather_file(tmp_path):
    path, wpath = tmp_path / "p.geojson", tmp_path / "w.json"
    path.write_text(json.dumps(feature_collection(squares())), encoding="utf-8")
    wpath.write_text(json.dumps(fixed_weather(BASE_AT, 9.0, 45, "KMA")), encoding="utf-8")
    result = FilePredictor(path, crs=5179, weather_path=wpath).predict(request())
    assert result.weather["source"] == "KMA"


def test_file_missing_hour_raises(tmp_path):
    path = tmp_path / "p.geojson"
    path.write_text(json.dumps(feature_collection(squares()[:7])), encoding="utf-8")
    with pytest.raises(ValueError, match="P\\[8\\]"):
        FilePredictor(path, crs=5179).predict(request())


def test_get_predictor_unknown_name():
    with pytest.raises(ValueError, match="모르는 예측"):
        get_predictor("replay")
