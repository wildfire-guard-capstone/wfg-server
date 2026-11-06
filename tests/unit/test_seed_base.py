from app.db.seed.base import parse_coverage, shelter_tier, to_int


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
