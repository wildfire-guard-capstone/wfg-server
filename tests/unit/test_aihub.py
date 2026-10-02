"""Uses a synthetic zip with the same structure as the AI Hub source data (no real data)."""

import json
import zipfile
from pathlib import Path

import pytest

from evaluation.datasets import aihub

# ~200 m x 200 m square around (128.0E, 35.0N) -> ~4 ha
SQUARE = "[35.0,128.0],[35.0,128.0022],[35.0018,128.0022],[35.0018,128.0]"


def source_doc(case_id: str, point: int, hour: int, wind_dir: float, humidity: float) -> dict:
    return {
        "raw_data_info": {
            "fire_info": {
                "fire_incident_id": case_id,
                "incident_name": "테스트 산불",
                "fire_location": {"lat": 35.0009, "lon": 128.0011},
                "start_timestamp": "2025-04-07 12:05:00",
                "end_timestamp": "2025-04-07 18:00:00",
                "response_phase": "1단계",
                "affected_area": 4.0,
                "fire_spread_track": SQUARE,
            }
        },
        "source_data_info": {
            "weather_conditions": {
                "observation_time": f"2025-04-07 {12 + hour}:00",
                "wind_speed": 2.0 + point,
                "wind_direction": wind_dir,
                "temperature": 20.0,
                "humidity_percent": humidity,
            }
        },
    }


@pytest.fixture
def aihub_root(tmp_path: Path) -> Path:
    zdir = tmp_path / "Training" / "01.원천데이터"
    zdir.mkdir(parents=True)
    with zipfile.ZipFile(zdir / "TS_소형_경상도.zip", "w") as zf:
        for hour in (1, 2):
            for point, wind_dir in ((1, 350.0), (2, 10.0)):
                humidity = 2.0 if hour == 1 else 40.0
                doc = source_doc("TT20250407", point, hour, wind_dir, humidity)
                zf.writestr(
                    f"TS_소형_경상도/TT20250407_S_P{point:04d}_T{hour:03d}.json",
                    json.dumps(doc, ensure_ascii=False),
                )
    return tmp_path


def test_parse_track_swaps_to_lonlat_and_closes_ring():
    ring = aihub.parse_track(SQUARE)
    assert ring[0] == (128.0, 35.0)
    assert ring[0] == ring[-1]
    assert len(ring) == 5


def test_polygon_area_is_close_to_expected():
    assert aihub.polygon_area_ha(aihub.parse_track(SQUARE)) == pytest.approx(4.0, rel=0.05)


def test_circular_mean_handles_north_wrap():
    mean = aihub.circular_mean_deg([350.0, 10.0])
    assert min(mean, 360.0 - mean) == pytest.approx(0.0, abs=1e-6)


def test_load_case_builds_hourly_weather(aihub_root: Path):
    case = aihub.load_case(aihub_root, "TT20250407")
    fc = case.fire_case
    assert fc.ignition.lon == pytest.approx(128.0011)
    assert len(fc.weather) == 2
    assert fc.weather[0].wind_speed_ms == pytest.approx(3.5)  # median of 3.0 and 4.0
    assert case.response_phase == "1단계"
    assert any("humidity" in w for w in case.warnings)


def test_missing_case_raises(aihub_root: Path):
    with pytest.raises(KeyError):
        aihub.load_case(aihub_root, "XX20000101")


def test_write_case_outputs(aihub_root: Path, tmp_path: Path):
    case = aihub.load_case(aihub_root, "TT20250407")
    target = aihub.write_case(case, tmp_path / "cases")
    names = {p.name for p in target.iterdir()}
    assert names == {"ignition.geojson", "perimeter.geojson", "weather.csv", "meta.json"}
    meta = json.loads((target / "meta.json").read_text())
    assert meta["perimeter_vertices"] == 4
