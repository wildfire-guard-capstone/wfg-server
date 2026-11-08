from datetime import UTC, datetime, timedelta, timezone

import pytest
from shapely.geometry import Point

from app.repositories.base_data import junction_kind, substation_kind, voltage_kv
from app.repositories.engine_inputs import (
    deployed_of,
    hourly_wind,
    kst,
    prev_alerts_of,
    site_of,
    village_fields,
)


def test_kst_converts_aware_and_keeps_naive():
    utc = datetime(2025, 3, 22, 2, 25, tzinfo=UTC)
    assert kst(utc) == datetime(2025, 3, 22, 11, 25)
    assert kst(datetime(2025, 3, 22, 11, 25)) == datetime(2025, 3, 22, 11, 25)
    assert kst(datetime(2025, 3, 22, 11, 25, tzinfo=timezone(timedelta(hours=9)))).hour == 11


def test_site_of_returns_lat_lon():
    lat, lon = site_of(Point(1_000_000, 2_000_000))  # EPSG:5179 원점 = 북위 38 · 동경 127.5
    assert lat == pytest.approx(38.0, abs=1e-6)
    assert lon == pytest.approx(127.5, abs=1e-6)


def test_hourly_wind_skips_missing_values():
    weather = {"hours": [{"wind_speed": 3, "wind_dir": 270}, {"wind_speed": None, "wind_dir": 90}]}
    assert hourly_wind(weather) == ({0: 3.0}, {0: 270.0, 1: 90.0})
    assert hourly_wind({}) == ({}, {})


def test_village_fields_keys_are_ids_and_ignore_unknown():
    out = village_fields({"villages": {"1024": {"evacuated": True, "injured": 3}}})
    assert out[1024].evacuated is True
    assert out[1024].evac_order is False


def test_deployed_maps_keys_to_labels_and_drops_total():
    d = deployed_of(
        {
            "deployed": {
                "helicopter": {"total": 2, "military": 2},
                "crew": {"total": 300, "special_crew": 187, "forest_officials": 113},
            }
        }
    )
    assert d.helicopter == {"군": 2}
    assert d.crew == {"진화대": 187, "공무원": 113}
    assert d.vehicle == {}
    assert deployed_of({}) is None


def test_deployed_unknown_key_raises():
    with pytest.raises(ValueError, match="모르는 키"):
        deployed_of({"deployed": {"helicopter": {"navy": 1}}})


def test_prev_alerts_from_context():
    ctx = {"alerts": {"areas": [{"sigungu": "의성군", "emd": "안평면", "level": "go"}]}}
    assert prev_alerts_of(ctx) == (("의성군", "안평면", "go"),)
    assert prev_alerts_of({}) == ()


def test_map_feature_kinds():
    assert junction_kind("낙동분기점") == "JC"
    assert junction_kind("의성 나들목") == "IC"
    assert junction_kind("상주IC") == "IC"
    assert voltage_kv("154000;22900") == 154
    assert voltage_kv(None) is None
    assert substation_kind({"type": "substation"}) == "변전소"
    assert substation_kind({"type": "plant", "source": "solar"}) == "태양광"
    assert substation_kind({"type": "plant", "source": "hydro"}) == "발전소"
