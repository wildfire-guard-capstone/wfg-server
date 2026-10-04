from datetime import datetime, timedelta

import pytest
from shapely.geometry import Point, Polygon

from spread.elmfire_inputs import (
    NAMELIST,
    CaseFiles,
    DeckConfig,
    hourly_weather,
    ignition_block,
    make_grid,
    simulation_hours,
)
from spread.geo import to_utm
from spread.units import ms_to_mph

T0 = datetime(2025, 4, 7, 12, 0)
# ~200 m x 200 m square around (128.0E, 35.0N)
SQUARE = Polygon([(128.0, 35.0), (128.0022, 35.0), (128.0022, 35.0018), (128.0, 35.0018)])


def make_case(weather=None, end_h: float | None = 5.5) -> CaseFiles:
    if weather is None:
        weather = [
            {"time": T0 + timedelta(hours=1), "ws_ms": 4.0, "wd_deg": 270.0},
            {"time": T0 + timedelta(hours=2), "ws_ms": 6.0, "wd_deg": 300.0},
        ]
    return CaseFiles(
        case_id="TEST0001",
        ignition_lonlat=(128.0011, 35.0009),
        ignition_time=T0,
        end_time=T0 + timedelta(hours=end_h) if end_h is not None else None,
        perimeter_wgs84=SQUARE,
        weather=weather,
    )


def test_grid_is_aligned_and_contains_fire():
    case, cfg = make_case(), DeckConfig()
    g = make_grid(case, cfg)
    assert g.epsg == 32652
    for v in (g.xmin, g.ymin, g.xmax, g.ymax):
        assert v % cfg.cell_m == pytest.approx(0.0, abs=1e-6)
    assert g.width == g.height
    assert g.width * cfg.cell_m >= cfg.min_domain_m  # small fire -> minimum domain
    perim = to_utm(SQUARE, g.epsg)
    ign = to_utm(Point(case.ignition_lonlat), g.epsg)
    minx, miny, maxx, maxy = perim.union(ign).bounds
    assert g.xmin < minx and maxx < g.xmax and g.ymin < miny and maxy < g.ymax


def test_grid_edges_aligned_for_odd_cell_count():
    # 3,030 m minimum domain = 101 cells: the old code put the edges half a cell off
    g = make_grid(make_case(), DeckConfig(min_domain_m=3030.0))
    for v in (g.xmin, g.ymin, g.xmax, g.ymax):
        assert v % 30.0 == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize(
    ("end_h", "expected"),
    [(5.5, 8.25), (2.0, 8.0), (None, 8.0), (60.0, 48.0)],
)
def test_simulation_hours_bounds(end_h, expected):
    assert simulation_hours(make_case(end_h=end_h), DeckConfig()) == pytest.approx(expected)


def test_hourly_weather_forward_fills_and_converts_to_mph():
    wx = hourly_weather(make_case(), 4)
    # hour 0 is before the first record -> first record; then latest record at or before
    assert [w[1] for w in wx] == [270.0, 270.0, 300.0, 300.0]
    assert wx[0][0] == pytest.approx(ms_to_mph(4.0))
    assert wx[3][0] == pytest.approx(ms_to_mph(6.0))


def test_hourly_weather_requires_records():
    with pytest.raises(ValueError):
        hourly_weather(make_case(weather=[]), 3)


def test_namelist_fills_run_values():
    text = NAMELIST.format(
        n_wx=9, ignition=ignition_block(354982.2, 3884367.9), tstop=28800.0, phiw=1.5, phis=1.0
    )
    assert "NUM_METEOROLOGY_TIMES = 9" in text
    assert "X_IGN(1)      = 354982.2" in text
    assert "SIMULATION_TSTOP = 28800.0" in text
    assert "SIMULATION_DT    = 10.0" in text  # 1 s triggers ELMFIRE's false "stalled" stop
    assert "PHIW_ADJ = 1.5" in text and "PHIS_ADJ = 1.0" in text
    assert "{" not in text
