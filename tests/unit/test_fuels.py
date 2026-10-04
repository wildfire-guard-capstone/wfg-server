from pathlib import Path

import pytest

from spread import fuels
from spread.elmfire_inputs import CaseFiles, DeckConfig, case_fuel, case_moisture

XWALK = fuels.load_crosswalk()
DEFAULT_M = {"m1": 6, "m10": 8, "m100": 10, "lh": 30, "lw": 60}


def pts(*types: str, moisture: str = "") -> list[dict]:
    return [{"fuel_type": t, "fuel_moisture": moisture} for t in types]


def test_crosswalk_v1_codes_are_valid_sb40():
    assert XWALK["침엽수림"] == 165
    assert XWALK["무립목지/비산림"] in fuels.NON_BURNABLE
    for code in XWALK.values():
        assert 91 <= code <= 204


def test_majority_ignores_non_burnable_and_unknown():
    p = pts("무립목지/비산림", "무립목지/비산림", "무립목지/비산림", "활엽수림", "모름")
    assert fuels.majority_fuel(p, XWALK, default=165) == XWALK["활엽수림"]


def test_majority_falls_back_to_default():
    assert fuels.majority_fuel([], XWALK, default=165) == 165
    assert fuels.majority_fuel(pts("무립목지/비산림"), XWALK, default=165) == 165


def test_majority_picks_most_common():
    p = pts("침엽수림", "초지", "초지", "활엽수림")
    assert fuels.majority_fuel(p, XWALK, default=165) == XWALK["초지"]


def test_dead_fuel_moisture_median_skips_missing_and_zero():
    p = [{"fuel_moisture": v} for v in ("0", "", "12", "14", "16.0")]
    m = fuels.dead_fuel_moisture(p, DEFAULT_M)
    assert m["m1"] == 14
    assert (m["m10"], m["m100"]) == (15, 16)
    assert (m["lh"], m["lw"]) == (30, 60)


def test_dead_fuel_moisture_fallback():
    assert fuels.dead_fuel_moisture(pts("침엽수림", moisture="0"), DEFAULT_M) == DEFAULT_M


def test_load_points_missing_file(tmp_path: Path):
    assert fuels.load_points(tmp_path / "points.csv") == []


def _case(points):
    from datetime import datetime

    from shapely.geometry import Point

    return CaseFiles("T", (128.0, 35.0), datetime(2025, 1, 1), None, Point(0, 0), [], points)


def test_deck_config_sources_and_label():
    case = _case(pts("초지", "초지", "침엽수림", moisture="12"))
    v0 = DeckConfig()
    assert case_fuel(case, v0) == 165
    assert case_moisture(case, v0) == v0.moisture_pct
    assert v0.label() == "fuel-fm165_moist-fixed_wind-1"

    cfg = DeckConfig(fuel_source="case_majority", moisture_source="aihub_points", wind_mult=0.6)
    assert case_fuel(case, cfg) == XWALK["초지"]
    assert case_moisture(case, cfg)["m1"] == 12
    assert cfg.label() == "fuel-case_majority_moist-aihub_points_wind-0.6"
    assert DeckConfig(phiw_adj=2.0).label() == "fuel-fm165_moist-fixed_wind-1_phiw-2_phis-1"


def test_unknown_source_raises():
    with pytest.raises(ValueError):
        case_fuel(_case([]), DeckConfig(fuel_source="nope"))


def test_canopy_medians_and_zero_fallback():
    p = [
        {"canopy_coverage": "75", "canopy_height": "18"},
        {"canopy_coverage": "35", "canopy_height": ""},
        {"canopy_coverage": "0", "canopy_height": "5"},
    ]
    assert fuels.canopy(p) == (55.0, 11.5)
    assert fuels.canopy([]) == (0.0, 0.0)


def test_canopy_source_option():
    from spread.elmfire_inputs import case_canopy

    case = _case([{"canopy_coverage": "60", "canopy_height": "12"}])
    assert case_canopy(case, DeckConfig()) == (0.0, 0.0)
    assert case_canopy(case, DeckConfig(canopy_source="aihub_points")) == (60.0, 12.0)
    assert DeckConfig(canopy_source="aihub_points").label().endswith("_canopy-aihub_points")
