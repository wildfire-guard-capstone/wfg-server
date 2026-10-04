import pytest

from spread import units


def test_wind_speed_round_trip():
    assert units.ms_to_mph(1.0) == pytest.approx(2.23694, rel=1e-5)
    assert units.mph_to_ms(units.ms_to_mph(7.3)) == pytest.approx(7.3)


def test_fuel_conversions_match_anderson_fm9():
    # Anderson FM9: 1-h load 2.92 t/ac = 0.134 lb/ft2 = 0.654 kg/m2; SAV 2500 1/ft; depth 0.2 ft
    assert units.kgm2_to_lbft2(0.654) == pytest.approx(0.134, rel=1e-2)
    assert units.inv_cm_to_inv_ft(82.0) == pytest.approx(2500, rel=1e-2)
    assert units.m_to_ft(0.06096) == pytest.approx(0.2, rel=1e-4)


def test_heat_content():
    # 8000 BTU/lb = 18608 kJ/kg
    assert units.kjkg_to_btulb(18608) == pytest.approx(8000, rel=1e-3)
