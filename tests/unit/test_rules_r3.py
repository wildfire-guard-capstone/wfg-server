"""11/19 평가 R3 + 11/20 사용자 결정(D1 다시 정함 — 경계 걸침도 대피 대상,
행정리는 구역에 불이 닿는 P) + 11/25(불에 가까운 행정리만):
마을 판정 · 행정리 이름 · 대피소 제외 · 본 화선 방향 · 회차 뒤 발화.

범위 반 변(m): P0 348 · P1 472 · P2 716 · P3 809 · P4 889 · P5 953 · P6 1012 · P7 1067 · P8 1117.
마을은 x 방향 띠(y −50 ~ 50 m)라 x 좌표 하나로 처음 닿는 P가 정해진다.
"""

from types import SimpleNamespace

from rules_example import full_inputs, inputs, place, square, village
from shapely.geometry import MultiPolygon, Point, box

from rules.bundles import build_bundles
from rules.bundles.spread import main_fire
from rules.context import build_context
from rules.core.villages import AdminVillage, Village, near_p, reached_villages
from rules.items.e1 import e1_input
from rules.items.e2 import e2_input
from rules.items.e4 import e4_input
from rules.items.names import village_label
from rules.items.requests import e_rows
from rules.sections.e2 import e2_sections
from rules.sections.e4 import e4_sections

RANGES = inputs().ranges
# 가리: 경계 P0(주택은 P6에만) → 리 경계 기준으로 이미 닿음. 행정리 하나(= 법정리)
# 나리: 경계 P0. 행정리 둘 — 나1리 구역(-1,250 ~ -300 m)은 P8 안,
#       나2리 구역(-4,000 ~ -3,200 m)은 P8에서 1 km 밖(11/25 — 대피 대상이 아님)
# 다리: 경계는 P7인데 주택이 1,250 m(8시간 밖)뿐 → 11/19 밤에는 「경계만 걸침」,
#       11/20부터 잠재 위험구역
# 라리: 경계 P1
VILLAGES = [
    village(11, "가리", "안평면", 300, 1050, 100),
    village(12, "나리", "안평면", -4000, -300, 200),
    village(13, "다리", "안계면", 1060, 1300, 300),
    Village(14, "라리", "안계면", "의성군", box(400, 100, 460, 200), 50, None),
]
HOUSES = ([1000.0, -500.0, -3500.0, 1250.0], [0.0, 0.0, 0.0, 0.0])
ADMIN = [
    AdminVillage(1, 12, "나1리", 1, "나1리회관", Point(-520, 0), box(-1250, -50, -300, 50)),
    AdminVillage(2, 12, "나2리", 2, "나2리경로당", Point(-3400, 0), box(-4000, -50, -3200, 50)),
    AdminVillage(3, 11, "가리", 1),  # 나뉘지 않은 리(행정리 = 법정리, 회관 · 구역 없음)
]


def _kw(**extra):
    return dict(villages=VILLAGES, houses=HOUSES, admin_villages=ADMIN, field={}, **extra)


def _bundles(**extra):
    return build_bundles(build_context(full_inputs(**_kw(**extra))))


def test_reached_by_boundary_edge_counts_and_admin_zone():
    hits, edges = reached_villages(VILLAGES, RANGES, HOUSES, ADMIN)
    got = {r.village.name: (r.p, r.boundary_p, r.houses) for r in hits}
    # 11/20 D1: 판정 P = 리 경계 P, 경계만 걸친 다리도 대상(주택은 참고 칸 houses_by_p)
    assert got == {"가리": (0, 0, 1), "나리": (0, 0, 2), "라리": (1, 1, 0), "다리": (7, 7, 1)}
    assert edges == []
    na = next(r for r in hits if r.village.name == "나리")
    assert na.zoned
    assert [(a.admin.name, a.p, a.houses) for a in na.admin] == [
        ("나1리", 0, 1),  # 구역이 P0에 닿음(그 행정리 전원 대피)
        ("나2리", None, 1),
    ]
    assert [a.admin.name for a in na.reached_admin] == ["나1리"]
    ga = next(r for r in hits if r.village.name == "가리")
    assert not ga.zoned and ga.reached_admin == []  # 행정리 하나 = 이름을 붙이지 않음


def test_villages_bundle_zones_names_and_edge():
    v = _bundles()["villages"]
    assert [r["name"] for r in v["burning"]] == ["가리", "나리"]
    assert [r["name"] for r in v["p5"]] == ["라리"]
    assert [r["name"] for r in v["p8"]] == ["다리"]
    na = v["burning"][1]
    assert na["boundary_p"] == 0 and na["first_p"] == 0
    assert [a["name"] for a in na["admin_villages"]] == ["나1리"]
    assert na["houses"] == {"total": 2, "p5": 1, "p8": 1, "by": "리 경계"}
    assert na["population"] == 100  # 200명 × 나1리 집 1 ÷ 2(추정, 11/25)
    assert v["edge"] == []
    assert v["totals"]["within_p5"] == {"count": 3, "population": 250}


def test_e1_e2_show_admin_names_and_edge_villages():
    b = _bundles()
    e1 = e1_input(b)
    assert "이미 닿음 안평면 가리 · 나리(나1리)" in e1["finding"]
    assert "잠재 위험구역 1곳: 안계면 다리" in e1["finding"]  # 경계만 걸쳐도 대상(11/20 D1)
    assert e1["facts"]["edge_only"] == [] and "경계만 걸침" not in e1["finding"]
    assert "admin_village:1" in e1["targets"] and "village:13" in e1["targets"]
    e2 = e2_input(b)
    assert [r["name"] for r in e2["facts"]["immediate"]] == ["가리", "나리(나1리)", "라리"]
    assert [r["name"] for r in e2["facts"]["pre_evac"]] == ["다리"]


def test_shelter_inside_evacuating_unit_is_excluded_and_origin_is_hall():
    shelters = [
        place(101, "나1리경로당", "경로당", -1200, tier="village", capacity=30, village_id=12),
        place(102, "먼경로당", "경로당", -3000, tier="village", capacity=40),
        place(103, "나2리학교", "학교", -3500, tier="hub", capacity=200, village_id=12),
        # 어느 구역도 아님 — 가장 가까운 나1리(대피 대상)로 본다(11/25)
        place(105, "나1리옆학교", "학교", -1300, tier="hub", capacity=90, village_id=12),
        place(104, "먼학교", "학교", -4000, tier="hub", capacity=300),
    ]
    sh = _bundles(shelters=shelters)["shelters"]
    na = next(r for r in sh["villages"] if r["village"] == "나리")
    assert na["origin"] == {"kind": "회관", "name": "나1리회관", "of": "나1리"}
    assert [s["name"] for s in na["village_sites"]] == ["먼경로당"]
    assert na["excluded"] == [
        {"name": "나1리경로당", "reason": "대피 대상 마을 안(나1리, 위험구역)"},
        {"name": "나1리옆학교", "reason": "대피 대상 마을 안(나1리, 위험구역)"},
    ]
    # 나1리경로당 자리는 8시간 밖이지만 대피하는 나1리 안이라 뺀다.
    # 나2리는 P8에서 1 km 밖이라 대피 대상이 아니다 → 그 안 학교는 쓴다
    assert [s["name"] for s in na["hub_sites"]] == ["나2리학교", "먼학교"]
    assert sh["counts"]["in_targets"] == 2 and sh["counts"]["in_p8"] == 0
    e4 = e4_input(_bundles(shelters=shelters))
    assert "대피 대상 마을 안 대피소 2곳" in e4["finding"]
    text = str(e4_sections(e4)["sections"])
    assert "**나리(나1리)**" in text and "거리는 나1리회관에서" in text


def test_vulnerable_near_fire_by_own_place_or_its_admin_village():
    vul = [
        place(61, "나1리요양원", "노인요양원", -600, village_id=12),  # 나1리 구역(P0에 닿음) 안
        place(62, "나2리요양원", "노인요양원", -3500, village_id=12),  # P8에서 1 km 밖
        place(64, "구역밖요양원", "노인요양원", -1275, village_id=12),  # 구역 밖, P8에서 158 m
        place(63, "다리요양원", "노인요양원", 1080, village_id=13),  # 자리가 P8에 닿음
    ]
    rows = _bundles(vulnerable=vul)["vulnerable"]["in_range"]
    assert [(r["name"], r["first_p"], r["touched"], r["admin_village"]) for r in rows] == [
        ("나1리요양원", 0, True, "나1리"),  # 자리는 P2지만 대피하는 행정리 안이라 함께
        ("구역밖요양원", 5, False, None),  # P5(953 m)에서 322 m(QA 4-6: 전에는 빠짐)
        ("다리요양원", 5, False, None),  # P8에 닿지만 P5에서 127 m라 위험구역
    ]


def test_near_p_bands():
    got = [near_p(Point(x, 0), RANGES) for x in (900, 1400, 1500, 2200)]
    # P5(953 m) 안 · P5에서 447 m · P5에서 547 m(P8에서 383 m) · P8에서 1,083 m
    assert got == [(5, True), (5, False), (8, False), (None, False)]


def test_village_keeps_only_near_admin_and_drops_when_none():
    # 마리: 경계는 P3에 닿는다. 마1리는 P8에서 283 m, 마2리는 1 km 밖
    ma = Village(15, "마리", "단촌면", "의성군", box(800, -3000, 900, 0), 400, 100)
    near = AdminVillage(5, 15, "마1리", 1, None, None, box(800, -1600, 900, -1400))
    far = AdminVillage(6, 15, "마2리", 2, None, None, box(800, -2400, 900, -2200))
    houses = ([850.0, 850.0, 850.0, 850.0], [-1500.0, -2300.0, -2310.0, -2320.0])
    b = build_bundles(
        build_context(full_inputs(villages=[ma], admin_villages=[near, far], houses=houses))
    )
    (row,) = b["villages"]["p5"]
    assert (row["name"], row["first_p"], row["boundary_p"]) == ("마리", 5, 3)
    assert (row["touched"], row["from"], row["until"]) == (False, None, "17:50")
    assert row["approach_km"] == 0.4
    assert (row["population"], row["pop65_est"]) == (100, 25)  # 집 4동 중 1동
    e2 = e2_input(b)
    (e2_row,) = e2["facts"]["immediate"]
    assert (e2_row["name"], e2_row["people"]) == ("마리(마1리)", 100)
    assert "17:50에 불이 0.4 km까지 접근: **마리(마1리)** 100명" in str(e2_sections(e2))
    # 가까운 행정리가 하나도 없으면 리를 뺀다
    assert reached_villages([ma], RANGES, houses, [far]) == ([], [])


def test_village_with_unknown_zone_is_kept():
    ma = Village(15, "마리", "단촌면", "의성군", box(800, -3000, 900, 0), 400, 100)
    admin = [
        AdminVillage(
            5, 15, "마1리", 1, "마1리회관", Point(850, -2700), box(800, -2800, 900, -2600)
        ),
        AdminVillage(6, 15, "마2리", 2),  # 구역을 모름 → 멀다고 할 수 없어 리 전체를 남긴다
    ]
    hits, edges = reached_villages([ma], RANGES, HOUSES, admin)
    assert [(r.village.name, r.p, r.population) for r in hits] == [("마리", 3, 400)] and edges == []


def test_request_table_names_village_heads():
    rows = e_rows(_bundles())
    org = next(r for r in rows if r["group"] == "마을 조직")
    assert org["agency"] == "안평면 가리 · 나리(나1리), 안계면 라리 · 다리 이장, 마을순찰대"


def test_direction_ignores_separate_fire():
    p0 = square(50)
    main = box(-500, -900, 500, 400)  # 현재 화선에서 남쪽으로 자람
    other = box(-15_100, -100, -14_900, 100)  # 15 km 서쪽, 따로 난 불
    got, rest = main_fire(p0, MultiPolygon([main, other]))
    assert got.equals(main) and len(rest) == 1
    ranges = [p0, *[MultiPolygon([main, other])] * 8]
    sp = build_bundles(build_context(inputs(ranges=ranges)))["spread"]
    assert sp["dir"] == "남"
    assert sp["separate"] == {"count": 1, "ha_p5": 4}


def test_village_label():
    assert village_label("신안리", ["신안1리"]) == "신안리(신안1리)"
    assert village_label("신월리", []) == "신월리"


def test_demo_drops_ignition_after_round_time():
    from demo.uiseong import step1_spread as s1

    x, y, t = s1.EXTRA_IGNITIONS[0]
    main = box(0, 0, 100, 100)
    future = Point(x, y).buffer(50)
    sp = s1.SpreadResult(
        calibration={},
        ranges=[main, MultiPolygon([main, future])],
        observed=main,
        areas_ha=[1.0, 1.8],
        validation=[],
    )
    early = s1.drop_future_ignitions(sp, SimpleNamespace(t_s=t - 60))
    assert early.ranges[1].equals(main) and early.areas_ha == [1.0, 1.0]
    assert early.extra["future_ignitions_dropped"][0]["ha_p8"] == 0.8
    late = s1.drop_future_ignitions(sp, SimpleNamespace(t_s=t + 60))
    assert late is sp
    assert s1.known_ignitions(SimpleNamespace(t_s=t - 60)) == ()
