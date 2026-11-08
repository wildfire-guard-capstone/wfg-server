"""risk/: 기준표 구간 · 겹침 비율 · 지형 계산 · 연료 표 · 시간별 기상 · R 조립(합성 자료)."""

import math

import numpy as np
import pytest
from shapely.geometry import Point, box

from risk import fuel, terrain
from risk.compute import RiskInputs, compute_risk
from risk.criteria import CSV_WEIGHT_SUM, STATIC_KEYS, VARIABLES, aspect_grade, grade
from risk.grid import StaticGrid
from risk.overlap import Grid, grid_weights, weighted_mean, zone_sum
from risk.weather import GridField, dead_fuel_moisture, emc_simard, summarize


def g1(key, v):
    return float(grade(key, [v])[0])


# ── 기준표 ──


def test_weights_and_variable_count():
    assert len(VARIABLES) == 23
    assert CSV_WEIGHT_SUM == pytest.approx(0.94)


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("temp", 10, 1), ("temp", 10.0001, 2), ("temp", 33, 4), ("temp", 33.01, 5),
        ("slope", 9.99, 1), ("slope", 10, 2), ("slope", 40, 5),
        ("precip", 0, 5), ("precip", 0.0001, 4), ("precip", 10, 2), ("precip", 10.01, 1),
        ("humidity", 45, 2), ("humidity", 45.01, 1), ("humidity", 10, 5),
        ("curvature", -0.3, 1), ("curvature", -0.1, 2), ("curvature", 0.0999, 3),
        ("curvature", 0.1, 4), ("curvature", 0.3, 5),
        ("tpi", -50, 1), ("tpi", -15, 2), ("tpi", 14.99, 3), ("tpi", 15, 4), ("tpi", 50, 5),
        ("water_dist", 0.049, 1), ("water_dist", 0.05, 2), ("water_dist", 1.0, 5),
        ("canopy_height", 20, 2), ("canopy_height", 20.5, 1), ("canopy_height", 6, 5),
        ("vulnerable", 50, 1), ("vulnerable", 51, 2),
        ("vulnerable", 999, 4), ("vulnerable", 1000, 5),
        ("pressure", 1000, 5), ("pressure", 1020.5, 1),
    ],
)  # fmt: skip
def test_grade_follows_csv_edges(key, value, expected):
    assert g1(key, value) == expected


def test_grade_keeps_nan():
    out = grade("wind", [np.nan, 2.0])
    assert math.isnan(out[0]) and out[1] == 1


def test_aspect_grade():
    a = aspect_grade([0, 350, 90, 150, 180, 220, 300, 10], flat=[0, 0, 0, 0, 0, 0, 0, 1])
    assert a.tolist() == [1, 1, 2, 4, 5, 4, 3, 3]


# ── 겹침 비율 ──


def test_grid_weights_partial_cells():
    grid = Grid(0, 100, 10, 10, 10)  # 0 ~ 100 m 정사각, 칸 값 = 행 × 10 + 열
    w = grid_weights(box(5, 85, 25, 95), grid)  # 열 0 · 1 · 2(5 · 10 · 5 m) × 행 0 · 1(5 · 5 m)
    assert w.frac.sum() == pytest.approx(1.0)
    assert w.covered == pytest.approx(1.0)
    assert sorted(np.round(w.frac, 3).tolist()) == [0.125] * 4 + [0.25] * 2
    vals = np.arange(100.0).reshape(10, 10)
    assert weighted_mean(vals, w) == pytest.approx(0.125 * (0 + 2 + 10 + 12) + 0.25 * (1 + 11))


def test_grid_weights_values_and_outside():
    grid = Grid(0, 20, 10, 2, 2)  # 칸 값 = [[1, 2], [3, 4]]
    vals = np.array([[1.0, 2.0], [3.0, 4.0]])
    w = grid_weights(box(0, 0, 20, 20), grid)
    assert weighted_mean(vals, w) == pytest.approx(2.5)
    w = grid_weights(box(10, 10, 30, 20), grid)  # 절반이 격자 밖, 안쪽은 오른쪽 위 칸
    assert w.covered == pytest.approx(0.5)
    assert weighted_mean(vals, w) == pytest.approx(2.0)
    vals[0, 1] = np.nan
    assert weighted_mean(vals, w) is None


def test_zone_sum_adds_overlap_share():
    zones = [(box(0, 0, 10, 10), 100.0), (box(20, 0, 30, 10), 40.0)]
    n, hits = zone_sum(zones, box(5, 0, 25, 10))
    assert n == pytest.approx(50 + 20)
    assert len(hits) == 2


# ── 지형 ──


def test_slope_aspect_plane():
    x = np.arange(20) * 30.0
    dem = np.tile(x * 0.5, (20, 1))  # 동쪽으로 올라감 → 서쪽을 바라봄
    slope, aspect, flat = terrain.slope_aspect(dem, 30.0)
    assert slope[10, 10] == pytest.approx(math.degrees(math.atan(0.5)))
    assert aspect[10, 10] == pytest.approx(270.0)
    assert not flat[10, 10]
    _, _, flat0 = terrain.slope_aspect(np.zeros((5, 5)), 30.0)
    assert flat0.all()


def test_curvature_tpi_relief_signs():
    yy, xx = np.mgrid[-15:16, -15:16] * 30.0
    hill = 500 - (xx**2 + yy**2) / 1000.0
    assert terrain.curvature(hill, 30.0)[15, 15] > 0  # 볼록 = +
    assert terrain.tpi(hill, 30.0)[15, 15] > 0  # 봉우리 = 주변보다 높음
    ramp = np.tile(np.arange(31) * 2.0, (31, 1))
    assert terrain.relief(ramp)[15, 15] == pytest.approx(12.0)  # 7칸 창 = 6칸 × 2 m


def test_flow_accumulation_valley():
    yy, xx = np.mgrid[0:21, 0:21]
    dem = np.abs(xx - 10) * 5.0 + (20 - yy) * 1.0  # 가운데 골짜기, 남쪽(아래)으로 흐름
    acc = terrain.flow_accumulation_km2(dem, 30.0)
    assert acc[20, 10] == acc.max()  # 골짜기 출구에 모인다
    assert acc[20, 10] > acc[10, 10] > acc[10, 0]


def test_water_distance():
    pytest.importorskip("scipy")
    mask = np.zeros((5, 5), bool)
    mask[2, 2] = True
    d = terrain.distance_km(mask, 100.0)
    assert d[2, 2] == 0 and d[2, 4] == pytest.approx(0.2)


# ── 연료 ──


def _deck(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "inputs" / "fuel_model_mapping.csv").write_text(
        "fbfm,수종,영급,경급,수고,지하고,연소가능수관연료밀도,h1,h10,h100,낙엽층,비고\n"
        "14,소나무,4.0,2.0,11.1,6.3,0.36,0.07,0.12,2.08,6.84,\n"
        "15,신갈나무,3.0,1.0,9.0,5.0,0.30,0.30,2.00,5.00,9.00,\n",
        encoding="utf-8-sig",
    )
    (tmp_path / "scripts" / "fuel_table_raw.csv").write_text(
        "수종,군,영급,경급,연소가능수관연료량,유기물층\n소나무,C,4.0,2.0,17.0,5.0\n신갈나무,B,3.0,1.0,20.0,6.0\n",
        encoding="utf-8-sig",
    )
    (tmp_path / "inputs" / "fuel_models.csv").write_text(
        "14,PD,.FALSE.,0.1,0.0,0.0,0,0,1800,9999,9999,0.3,30,8000\n"
        "15,QM,.FALSE.,0.1,0.0,0.0,0,0,2000,9999,9999,0.3,25,8000\n"
        "91,NB1,.FALSE.,0,0,0,0,0,9999,9999,9999,1.0,10,8000\n"
        "102,GR2,.TRUE.,0.0046,0,0,0.0459,0,2000,1800,9999,1.0,15,8000\n",
        encoding="utf-8",
    )
    return tmp_path


def test_load_fuel_models(tmp_path):
    m = fuel.load_fuel_models(_deck(tmp_path))
    pine = m[14]
    assert pine.type == "침엽수림" and pine.age_years == 35
    assert pine.load_t_ha == pytest.approx(17.0 + 0.07 + 0.12 + 2.08 + 6.84 + 5.0)
    assert pine.depth_cm == pytest.approx(0.3 * 30.48, abs=0.01)
    assert m[15].type == "활엽수림"
    grass = m[102]
    assert grass.type == "초지" and grass.load_t_ha == pytest.approx(0.0505 / 0.020482, rel=1e-3)
    assert m[91].type == "비연소" and m[91].depth_cm == 0


def test_fuel_layers_and_canopy(tmp_path):
    m = fuel.load_fuel_models(_deck(tmp_path))
    fb = np.array([[14, 102], [91, 7]])
    layers = fuel.fuel_layers(fb, m)
    assert fuel.fuel_type_grade(layers["fuel_type"])[0].tolist() == [4, 5]
    assert fuel.fuel_type_grade(layers["fuel_type"])[1, 0] == 1  # 비연소
    assert math.isnan(layers["fuel_load"][1, 1])  # 사전에 없는 번호
    g = fuel.canopy_height_grade(np.array([0.0, 5.0, 25.0]), np.array([0.0, 55.0, 85.0]))
    assert g.tolist() == [1, 5, 1]  # 수관 없음 = 1, 낮은 수관 = 5


# ── 기상 · 연료 수분 ──


def test_summarize_point_mean_and_rain_sum():
    geom = box(0, 0, 10, 10)
    temps = {h: v for h, v in enumerate([5, 15, 15, 25, 25, 35])}  # 등급 1 2 2 3 3 5
    s = summarize("temp", temps, geom)
    assert s.grade == pytest.approx(16 / 6)
    assert summarize("temp", temps, geom, mode="at_p5").grade == 5
    rain = {1: 0.0, 2: 0.5, 3: 0.5, 4: 0.0, 5: 0.0}
    s = summarize("precip", rain, geom)
    assert s.value == 1.0 and s.grade == 4
    assert summarize("temp", {0: 10.0}, geom).grade is None  # 빈 시각은 메우지 않는다


def test_summarize_grid_field():
    grid = Grid(0, 20, 10, 2, 2)
    f = GridField(np.array([[2.0, 2.0], [8.0, 8.0]]), grid)  # 위 칸 1등급, 아래 칸 3등급
    s = summarize("wind", dict.fromkeys(range(6), f), box(0, 0, 20, 20))
    assert s.grade == pytest.approx(2.0) and s.value == pytest.approx(5.0)


def test_dead_fuel_moisture():
    assert emc_simard(25, 20) == pytest.approx(2.22749 + 0.160107 * 20 - 0.01478 * 77)
    m = dead_fuel_moisture([25] * 3, [20] * 3, [0, 1.0, 0])
    e = emc_simard(25, 20)
    assert m[0] == pytest.approx(e)
    assert m[1] == pytest.approx(35.0 + (e - 35.0) * math.exp(-1))  # 비 0.5 mm 이상 → 35%로 다가감
    assert m[2] < m[1]


# ── R 조립 ──


def _static(grade_value=3.0, h=4, w=4):
    grid = Grid(0, 40, 10, h, w)
    grades = {k: np.full((h, w), grade_value) for k in STATIC_KEYS}
    values = {k: np.full((h, w), 1.0) for k in STATIC_KEYS}
    return StaticGrid(grid, values, grades)


def test_compute_risk_weighted_mean_and_missing():
    p5 = box(0, 0, 40, 40)
    weather = {
        "temp": dict.fromkeys(range(6), 30.0),  # 4
        "wind": dict.fromkeys(range(6), 12.0),  # 4
        "humidity": dict.fromkeys(range(6), 15.0),  # 4
        "moisture": dict.fromkeys(range(6), 8.0),  # 5
        "precip": dict.fromkeys(range(1, 6), 0.0),  # 5
    }
    res = compute_risk(
        RiskInputs(
            p5=p5,
            perimeter=Point(20, 20),
            static=_static(3.0),
            weather=weather,
            rain_7d=0.0,  # 5
            zones=[(box(0, 0, 40, 40), 300.0)],  # 3
            stations=[Point(20, 20 + 5000)],  # 5 km → 2
        )
    )
    used = {f.key: f.grade for f in res.factors if f.grade is not None}
    assert set(res.missing) == {"pressure", "ground_temp"}
    w = {v.key: v.weight for v in VARIABLES}
    expect = sum(w[k] * g for k, g in used.items()) / sum(w[k] for k in used)
    assert res.score == pytest.approx(expect)
    assert sum(f.weight for f in res.factors) == pytest.approx(1.0)
    assert used["vulnerable"] == 3 and used["station_dist"] == 2
    assert res.used_weight_csv == pytest.approx(0.94 - 0.06)
    d = res.to_dict()
    assert d["score"] == round(res.score, 2) and len(d["factors"]) == 23


def test_compute_risk_without_static_grid():
    res = compute_risk(RiskInputs(p5=box(0, 0, 10, 10), perimeter=box(0, 0, 1, 1), static=None))
    assert res.score is None and len(res.missing) == 23
