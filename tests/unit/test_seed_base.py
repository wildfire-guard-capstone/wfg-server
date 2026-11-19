import json

from app.db.seed.base import admin_village_row, parse_coverage, shelter_tier, to_int


def test_admin_village_row():
    zone = {
        "type": "MultiPolygon",
        "coordinates": [[[[128.6, 36.4], [128.61, 36.4], [128.6, 36.41], [128.6, 36.4]]]],
    }
    f = {
        "type": "Feature",
        "geometry": zone,
        "properties": {
            "bjd_cd": "4773046035", "name": "괴산1리", "seq": 1, "hall_name": "괴산1리마을회관",
            "hall_lon": 128.61, "hall_lat": 36.40, "hall_source": "카카오맵", "houses_est": 24,
            "attrs": {"method": {"anchor": 50}},
        },
    }  # fmt: skip
    row = admin_village_row(f, {"4773046035": 7})
    assert row["village"] == 7 and row["seq"] == 1 and row["houses"] == 24
    assert json.loads(row["zone"]) == zone and json.loads(row["attrs"]) == {
        "method": {"anchor": 50}
    }
    # 나뉘지 않은 법정리: 회관 · 구역 없음
    bare = {
        "type": "Feature",
        "geometry": None,
        "properties": {"bjd_cd": "4773046035", "name": "신월리", "seq": 1},
    }
    row = admin_village_row(bare, {"4773046035": 7})
    assert row["zone"] is None and row["lon"] is None and row["hall_name"] is None
    assert row["attrs"] == "{}"


def test_shelter_tier():
    assert shelter_tier("경로당") == "village"
    assert shelter_tier("학교") == "hub"
    assert shelter_tier("면사무소") == "hub"
    # Motels are dropped from shelter candidates (11/4).
    assert shelter_tier("숙박시설") is None


def test_parse_coverage():
    assert parse_coverage("대구광역시;경상북도 안동시;군위군;") == [
        (None, "대구광역시"),
        ("경상북도", "안동시"),
        (None, "군위군"),
    ]
    assert parse_coverage("") == []


def test_to_int():
    assert to_int("375") == 375
    assert to_int("12.0") == 12
    assert to_int("") is None
