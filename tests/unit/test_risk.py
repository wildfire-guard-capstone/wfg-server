from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import Point, box

from app.services import risk as svc
from app.services.risk import FACTORS, KEY_OF, FactorGrade, aggregate
from risk import compute as calc
from risk import grid as grid_mod
from risk.criteria import BY_KEY, EXPOSURE_BY_KEY, STATIC_KEYS
from risk.grid import StaticGrid
from risk.overlap import Grid

BASE_AT = datetime.fromisoformat("2025-03-22T12:00:00+09:00")
WEATHER_CODES = {"wind_speed", "humidity", "temperature", "precipitation", "fuel_moisture"}


def grades(value):
    return [FactorGrade(code, None, value, None, None, None) for code, *_ in FACTORS]


def weather(over=None, n=9):
    hours = [
        {
            "at": (BASE_AT + timedelta(hours=i)).isoformat(),
            "kind": "고정",
            "wind_dir": 270.0,
            "wind_speed": 6.0,
            "temp": 18.0,
            "humidity": 25.0,
            "precip": 0.0,
        }
        for i in range(n)
    ]
    for (i, k), v in (over or {}).items():
        hours[i][k] = v
    return {"source": "FIXED", "issued_at": None, "location": None, "hours": hours}


def result(w, zones=(), stations=(), static=None, houses=None):
    timed, rain = svc.weather_inputs(w)
    inp = calc.RiskInputs(
        p5=box(0, 0, 2000, 1000),
        perimeter=Point(100, 500),
        static=static,
        weather=timed,
        rain_7d=rain,
        zones=zones,
        stations=stations,
        houses_xy=houses,
        exposure_radius_m=svc.EXPOSURE_RADIUS_M,
        combine="sqrt_hx",
    )
    return calc.compute_risk(inp)


def with_grade(gs, code, value):
    return [FactorGrade(g.code, None, value, None, None, None) if g.code == code else g for g in gs]


def test_weights_are_ai_hub_20_plus_scale_and_houses():
    # risk-3(11/25): 지형위치지수 · 곡률 · 지형복잡도를 뺌, 지형복잡도 0.03은 경사도_up에 합침
    assert len(FACTORS) == 22
    codes = {f[0] for f in FACTORS}
    assert not codes & {"curvature", "terrain_complexity", "topographic_position"}
    assert dict((f[0], f[3]) for f in FACTORS)["slope_up"] == pytest.approx(0.11)
    assert sum(f[3] for f in FACTORS) == pytest.approx(1.08)
    assert sum(f[3] for f in FACTORS if f[2] in svc.HAZARD_AXES) == pytest.approx(0.96)
    assert sum(f[3] for f in FACTORS if f[2] in svc.EXPOSURE_AXES) == pytest.approx(0.12)


def test_codes_map_to_risk_variables_with_the_same_weights():
    assert set(KEY_OF) == {f[0] for f in FACTORS}
    assert len(set(KEY_OF.values())) == 22
    for code, _, _, weight in FACTORS:
        var = BY_KEY.get(KEY_OF[code]) or EXPOSURE_BY_KEY[KEY_OF[code]]
        assert var.weight == pytest.approx(weight), code


def test_all_same_grade_gives_that_score_and_contributions_add_up_per_group():
    r = aggregate(grades(4))
    assert r["status"] == "READY" and r["score"] == 4.0
    assert r["hazard"] == 4.0 and r["exposure"] == 4.0
    assert r["included_weight_sum"] == pytest.approx(1.08)
    for axes, total in ((svc.HAZARD_AXES, r["hazard"]), (svc.EXPOSURE_AXES, r["exposure"])):
        part = sum(f["contribution"] for f in r["factors"] if f["axis"] in axes)
        assert part == pytest.approx(total, abs=1e-3)
    assert set(r["axes"].values()) == {4.0}


def test_score_is_geometric_mean_of_hazard_and_exposure():
    g = grades(4)
    for code, axis in ((f[0], f[2]) for f in FACTORS):
        if axis in svc.EXPOSURE_AXES:
            g = with_grade(g, code, 1)
    r = aggregate(g)  # 조건이 나빠도(H 4) 사람 · 집이 없으면(X 1) R은 2
    assert r["hazard"] == 4.0 and r["exposure"] == 1.0 and r["score"] == 2.0


def test_without_exposure_r_is_hazard():
    g = grades(3)
    for code, axis in ((f[0], f[2]) for f in FACTORS):
        if axis in svc.EXPOSURE_AXES:
            g = with_grade(g, code, None)
    r = aggregate(g)
    assert r["status"] == "PARTIAL" and r["exposure"] is None and r["score"] == r["hazard"] == 3.0


def test_missing_variables_leave_the_denominator():
    g = grades(5)
    g[0] = FactorGrade("slope_up", None, None, "자료 없음", None, None)  # 0.11
    r = aggregate(g)
    assert r["status"] == "PARTIAL" and r["score"] == 5.0  # 빠진 가중치로 나누지 않는다
    assert r["included_weight_sum"] == pytest.approx(0.97)
    slope = next(f for f in r["factors"] if f["code"] == "slope_up")
    assert slope["included"] is False and slope["contribution"] is None


def test_nothing_included_is_unavailable_not_zero():
    r = aggregate(grades(None))
    assert r["status"] == "UNAVAILABLE" and r["score"] is None


def test_forecast_hours_go_to_0_5_and_rain_to_1_5():
    f = svc.forecast_inputs(weather({(3, "temp"): 21.0, (2, "precip"): 1.5}))
    assert set(f) == {"temp", "humidity", "wind", "precip"}
    assert sorted(f["temp"]) == [0, 1, 2, 3, 4, 5] and f["temp"][3] == 21.0
    assert sorted(f["precip"]) == [1, 2, 3, 4, 5] and f["precip"][2] == 1.5
    assert svc.forecast_inputs({})["wind"] == dict.fromkeys(range(6))


def test_without_grid_weather_people_station_scale_houses_are_real_and_the_rest_has_reasons():
    houses = np.array([[1000, 500], [5000, 500], [6000, 500], [7000, 500]], dtype="float64")
    # 리 주택 4채 중 P5 + 1 km 안 1채 → 200명 × 1/4 = 50명(면적 비율이면 3/8)
    zones = svc.with_houses([(box(0, 0, 8000, 1000), 200.0)], houses)
    res = result(weather(), zones=zones, stations=[Point(3100, 500)], houses=houses)  # 3 km
    gs = svc.to_grades(res, base_at=BASE_AT, weather_source="FIXED", grid_reason=svc.NO_GRID)
    by = {g.code: g for g in gs}
    included = {g.code for g in gs if g.grade is not None}
    assert included == WEATHER_CODES | {
        "vulnerable_population",
        "fire_station_distance",
        "fire_area",
        "houses_nearby",
    }
    assert by["vulnerable_population"].raw_value == pytest.approx(50.0)
    assert by["houses_nearby"].raw_value == 1.0
    assert by["fire_area"].raw_value == pytest.approx(200.0)  # 2 km × 1 km
    assert by["fire_station_distance"].raw_value == pytest.approx(3.0)
    assert by["wind_speed"].raw_value == pytest.approx(6.0)
    assert by["wind_speed"].reason == svc.FIXED_WEATHER and by["wind_speed"].value_at == BASE_AT
    assert all(by[c].reason == svc.NO_GRID for c, k in KEY_OF.items() if k in STATIC_KEYS)
    assert by["pressure"].reason == svc.MISSING_REASON["pressure"]

    r = aggregate(gs)
    assert r["status"] == "PARTIAL" and 1 <= r["score"] <= 5
    assert r["included_weight_sum"] == pytest.approx(0.26 + 0.06 + 0.07 + 0.15 + 0.05)
    assert r["score"] == pytest.approx(res.score, abs=0.01)  # risk.compute와 같은 값
    assert r["hazard"] == pytest.approx(res.hazard, abs=0.01)
    assert r["exposure"] == pytest.approx(res.exposure, abs=0.01)
    assert r["axes"]["terrain"] is None and r["axes"]["fuel"] == 5.0  # 수분함량만


def test_with_houses_attaches_only_houses_inside_each_zone():
    houses = np.array([[1, 1], [5, 5], [50, 50]], dtype="float64")
    z = svc.with_houses([(box(0, 0, 10, 10), 7.0), (box(100, 100, 110, 110), 3.0)], houses)
    assert len(z[0]) == 3 and len(z[0][2]) == 2
    assert z[1] == (box(100, 100, 110, 110), 3.0)  # 주택 없는 리는 면적 비율
    assert svc.with_houses([(box(0, 0, 1, 1), 1.0)], np.empty((0, 2))) == [(box(0, 0, 1, 1), 1.0)]


def test_surface_observation_completes_the_weather_factors():
    """지상관측 값 하나를 0 ~ 5시간에 쓴다."""
    seen = {"station": "278", "pressure": 1019.0, "ground_temp": 10.2, "rain_7d": 3.1}
    w = weather() | {"source": "KMA_STATIONS", "surface": seen}
    timed, rain = svc.weather_inputs(w)
    assert timed["pressure"] == dict.fromkeys(range(6), 1019.0) and rain == 3.1
    gs = svc.to_grades(
        result(w), base_at=BASE_AT, weather_source="KMA_STATIONS", grid_reason=svc.NO_GRID
    )
    by = {g.code: g for g in gs}
    assert (by["pressure"].grade, by["ground_temperature"].grade) == (2.0, 1.0)
    assert by["precipitation_7d"].grade == 4.0 and by["fuel_moisture"].grade == 5.0
    assert by["precipitation_7d"].raw_value == 3.1 and by["pressure"].value_at == BASE_AT
    assert not any(by[c].grade is None for c, k in KEY_OF.items() if BY_KEY.get(k, None)
                   and BY_KEY[k].axis == "W")  # fmt: skip

    part = w | {"surface": seen | {"ground_temp": None}}
    by = {
        g.code: g
        for g in svc.to_grades(
            result(part), base_at=BASE_AT, weather_source="KMA_STATIONS", grid_reason=svc.NO_GRID
        )
    }
    assert by["ground_temperature"].reason == svc.NO_SURFACE and by["pressure"].grade == 2.0
    assert svc.weather_inputs({})[0]["pressure"] == {} and svc.weather_inputs({})[1] is None


def test_moisture_is_what_elmfire_used_or_is_worked_out_from_the_saved_weather():
    used = weather({(i, "fuel_moisture_1h"): 12.0 + i for i in range(9)})
    assert svc.moisture_inputs(used) == ({h: 12.0 + h for h in range(6)}, True)
    part = weather({(i, "fuel_moisture_1h"): 12.0 for i in range(3)})  # 일부만 저장 → 섞지 않음
    worked, from_elmfire = svc.moisture_inputs(part)
    assert from_elmfire is False and sorted(worked) == list(range(6)) and worked[0] < 10
    assert svc.moisture_inputs(weather({(2, "humidity"): None})) == ({}, False)
    assert svc.moisture_inputs({}) == ({}, False)
    gs = svc.to_grades(
        result(used), base_at=BASE_AT, weather_source="KMA_STATIONS", grid_reason=svc.NO_GRID
    )
    moist = next(g for g in gs if g.code == "fuel_moisture")
    # 12 ~ 15% = 4등급 네 시각, 16 · 17% = 3등급 두 시각
    assert moist.grade == pytest.approx((4 * 4 + 3 * 2) / 6, abs=0.01)
    assert moist.raw_value == pytest.approx(14.5) and moist.value_at == BASE_AT


def test_a_gap_in_the_forecast_drops_that_variable():
    res = result(weather({(4, "humidity"): None}))
    gs = svc.to_grades(res, base_at=BASE_AT, weather_source="KMA", grid_reason=svc.NO_GRID)
    hum = next(g for g in gs if g.code == "humidity")
    assert hum.grade is None and hum.reason == svc.GAP_IN_FORECAST
    wind = next(g for g in gs if g.code == "wind_speed")
    assert wind.grade is not None and wind.reason is None  # 고정값이 아니면 대체 사유 없음


def test_reasons_are_grouped():
    res = result(weather())
    gs = svc.to_grades(res, base_at=BASE_AT, weather_source="FIXED", grid_reason=svc.NO_GRID)
    reasons = svc.unavailable_reasons(gs)
    grid = next(r for r in reasons if r.startswith(svc.NO_GRID))
    assert "경사도_up" in grid and "수관피복률" in grid
    assert any(r.startswith(svc.FIXED_WEATHER) and "풍속" in r for r in reasons)
    assert len(reasons) == len({r.split(":")[0] for r in reasons})


def _saved_grid(folder):
    """10 x 10칸(30 m) 시험 격자: 모든 변수 등급 4."""
    g = Grid(0.0, 300.0, 30.0, 10, 10)
    ones = np.ones((10, 10))
    grid_mod.save(
        StaticGrid(
            g,
            {k: ones for k in STATIC_KEYS},
            {k: ones * 4 for k in STATIC_KEYS},
            {"region": "시험"},
        ),
        folder,
    )
    return folder


def test_static_grid_is_optional(monkeypatch, tmp_path):
    def use(folder):
        monkeypatch.setattr(svc, "get_settings", lambda: SimpleNamespace(risk_grid_dir=folder))

    use(None)
    assert svc.static_grid((0, 0, 1, 1)) == (None, svc.NO_GRID)
    use(tmp_path / "없는 폴더")  # EC2에 아직 안 올림 — API는 그대로 뜬다
    grid, reason = svc.static_grid((0, 0, 1, 1))
    assert grid is None and reason.startswith(svc.NO_GRID) and "파일이 없음" in reason
    broken = tmp_path / "깨짐"
    broken.mkdir()
    (broken / "meta.json").write_text("{}", encoding="utf-8")
    use(broken)
    grid, reason = svc.static_grid((0, 0, 1, 1))
    assert grid is None and reason.startswith("지역 격자를 읽지 못함")


def test_static_grid_reads_only_the_p5_window(monkeypatch, tmp_path):
    monkeypatch.setattr(
        svc, "get_settings", lambda: SimpleNamespace(risk_grid_dir=_saved_grid(tmp_path / "g"))
    )
    p5 = box(35, 35, 95, 125)
    grid, reason = svc.static_grid(p5.bounds)
    assert reason is None and (grid.grid.h, grid.grid.w) == (4, 3)
    assert svc.static_grid((5000, 5000, 6000, 6000)) == (None, svc.OUT_OF_GRID)

    inp = calc.RiskInputs(
        p5=p5, perimeter=Point(40, 40), static=grid, weather=svc.forecast_inputs(weather())
    )
    gs = svc.to_grades(
        calc.compute_risk(inp), base_at=BASE_AT, weather_source="KMA", grid_reason=None
    )
    by = {g.code: g for g in gs}
    assert by["slope_up"].grade == 4 and by["canopy_cover"].grade == 4
    assert aggregate(gs)["axes"]["terrain"] == 4.0
