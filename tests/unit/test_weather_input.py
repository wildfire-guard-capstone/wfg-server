"""서버 기상(9시간) → ELMFIRE 기상 입력(spread.weather_input)."""

from datetime import datetime

import pytest

from app.weather.base import WeatherRequest
from app.weather.fixed import FixedWeather
from spread.weather_input import (
    dead_fuel_moisture,
    emc_percent,
    hour_of_year,
    to_elmfire,
)

BASE_AT = datetime.fromisoformat("2025-03-22T12:50:00+09:00")


def weather(**over):
    w = FixedWeather(**over).fetch(WeatherRequest((36.36, 128.60), BASE_AT))
    return w


def test_emc_matches_simard_three_branches():
    # 의성 덱(build_wx)과 같은 식. 21 ℃(69.8 ℉), 습도 24 % → 2.22749 + 0.160107·24 − 0.01478·69.8
    assert emc_percent(21, 24) == pytest.approx(5.04, abs=0.01)
    assert emc_percent(20, 5) == pytest.approx(0.03229 + 0.281073 * 5 - 0.000578 * 5 * 68, abs=1e-3)
    wet = 21.0606 + 0.005565 * 80**2 - 0.00035 * 80 * 50 - 0.483199 * 80
    assert emc_percent(10, 80) == pytest.approx(wet, abs=1e-3)
    assert emc_percent(40, 0) == 1.0  # 하한


def test_moisture_lags_toward_equilibrium():
    dry = {"temp": 25.0, "humidity": 15.0, "precip": 0.0}
    hours = [dict(dry) for _ in range(9)]
    m = dead_fuel_moisture(hours, init={"m1": 20.0, "m10": 20.0, "m100": 20.0})
    e = emc_percent(25, 15)
    # 1시간 연료는 금방, 100시간 연료는 거의 그대로
    assert m["m1"][8] == pytest.approx(e, abs=0.01)
    assert m["m10"][0] == 20.0 and e < m["m10"][8] < 20.0
    assert m["m100"][8] > 18.0
    assert all(a >= b for a, b in zip(m["m10"], m["m10"][1:], strict=False))


def test_rain_sets_wet_equilibrium_and_default_init_is_hour0_emc():
    hours = [{"temp": 20.0, "humidity": 30.0, "precip": 0.0} for _ in range(9)]
    hours[3]["precip"] = 2.0
    m = dead_fuel_moisture(hours)
    e0 = emc_percent(20, 30)
    assert m["m1"][0] == m["m10"][0] == m["m100"][0] == pytest.approx(e0, abs=1e-3)
    assert m["m1"][3] > 20  # 비 온 시간은 젖는다(상한 35 쪽으로)


def test_to_elmfire_units_and_bands():
    w = weather(wind_ms=5.0, wind_deg=270.0, temp=21.0, humidity=24.0)
    wx = to_elmfire(w)
    assert wx.t0.isoformat() == "2025-03-22T12:00:00+09:00"
    assert len(wx.ws_mph) == 9 and wx.ws_mph[0] == pytest.approx(11.185, abs=1e-3)  # 5 m/s
    assert wx.wd_deg == [270.0] * 9
    assert set(wx.bands) == {"ws", "wd", "m1", "m10", "m100"}
    g = wx.grids((3, 4))
    assert g["ws"].shape == (9, 3, 4) and g["ws"].dtype.name == "float32"
    assert float(g["wd"][4, 2, 3]) == 270.0
    assert to_elmfire(w, wind_mult=2.0).ws_mph[0] == pytest.approx(2 * wx.ws_mph[0], abs=0.01)


def test_to_elmfire_rejects_gaps():
    w = weather()
    w["hours"][4]["at"] = "2025-03-22T17:30:00+09:00"
    with pytest.raises(ValueError, match="1시간 간격"):
        to_elmfire(w)


def test_hour_of_year_matches_uiseong_deck():
    assert hour_of_year(datetime(2025, 3, 22, 11)) == 1931  # 덱 namelist HOUR_OF_YEAR
    assert hour_of_year(datetime(2025, 1, 1, 0)) == 0
