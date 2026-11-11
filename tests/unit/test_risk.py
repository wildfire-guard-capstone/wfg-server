from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import Point, box

from app.services import risk as svc
from app.services.risk import FACTORS, KEY_OF, FactorGrade, aggregate
from risk import compute as calc
from risk import grid as grid_mod
from risk.criteria import BY_KEY, STATIC_KEYS
from risk.grid import StaticGrid
from risk.overlap import Grid

BASE_AT = datetime.fromisoformat("2025-03-22T12:00:00+09:00")
WEATHER_CODES = {"wind_speed", "humidity", "temperature", "precipitation"}


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


def result(w, zones=(), stations=(), static=None):
    inp = calc.RiskInputs(
        p5=box(0, 0, 2000, 1000),
        perimeter=Point(100, 500),
        static=static,
        weather=svc.forecast_inputs(w),
        zones=zones,
        stations=stations,
        **svc.SPEC_OPTIONS,
    )
    return calc.compute_risk(inp)


def test_weights_sum_to_094():
    assert len(FACTORS) == 23
    assert sum(f[3] for f in FACTORS) == pytest.approx(0.94)


def test_spec_codes_map_to_risk_variables_with_the_same_weights():
    assert set(KEY_OF) == {f[0] for f in FACTORS}
    assert len(set(KEY_OF.values())) == 23
    for code, _, _, weight in FACTORS:
        assert BY_KEY[KEY_OF[code]].weight == pytest.approx(weight), code


def test_all_same_grade_gives_that_score_and_contributions_add_up():
    r = aggregate(grades(4))
    assert r["status"] == "READY" and r["score"] == 4.0
    assert r["included_weight_sum"] == pytest.approx(0.94)
    assert sum(f["contribution"] for f in r["factors"]) == pytest.approx(4.0, abs=1e-3)
    assert set(r["axes"].values()) == {4.0}


def test_missing_variables_leave_the_denominator():
    g = grades(5)
    g[0] = FactorGrade("slope_up", None, None, "자료 없음", None, None)  # 가중치 0.08
    r = aggregate(g)
    assert r["status"] == "PARTIAL" and r["score"] == 5.0  # 0.94로 나누지 않는다
    assert r["included_weight_sum"] == pytest.approx(0.86)
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


def test_without_grid_weather_people_and_station_are_real_and_the_rest_has_reasons():
    zones = [(box(0, 0, 4000, 1000), 200.0)]  # P5가 구역의 절반 → 100명
    res = result(weather(), zones=zones, stations=[Point(3100, 500)])  # 3 km
    gs = svc.to_grades(res, base_at=BASE_AT, weather_source="FIXED", grid_reason=svc.NO_GRID)
    by = {g.code: g for g in gs}
    included = {g.code for g in gs if g.grade is not None}
    assert included == WEATHER_CODES | {"vulnerable_population", "fire_station_distance"}
    assert by["vulnerable_population"].raw_value == pytest.approx(100.0)
    assert by["fire_station_distance"].raw_value == pytest.approx(3.0)
    assert by["wind_speed"].raw_value == pytest.approx(6.0)
    assert by["wind_speed"].reason == svc.FIXED_WEATHER and by["wind_speed"].value_at == BASE_AT
    assert all(by[c].reason == svc.NO_GRID for c, k in KEY_OF.items() if k in STATIC_KEYS)
    assert by["pressure"].reason == svc.MISSING_REASON["pressure"]

    r = aggregate(gs)
    assert r["status"] == "PARTIAL" and 1 <= r["score"] <= 5
    assert r["included_weight_sum"] == pytest.approx(0.33)
    assert sum(f["contribution"] for f in r["factors"] if f["included"]) == pytest.approx(
        r["score"], abs=0.01
    )
    assert r["axes"]["terrain"] is None and r["axes"]["fuel"] is None


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
