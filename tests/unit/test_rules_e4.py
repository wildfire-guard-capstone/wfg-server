"""E4: 대피소 후보(J8 · 종류별 가까운 2곳 · 예외) · 큰 도로 구간(핵심 경우만)."""

from rules_example import VILLAGES, inputs, place
from shapely.geometry import LineString

from rules.bundles import build_bundles
from rules.context import build_context
from rules.core.places import RoadPiece
from rules.items.e4 import e4_input
from rules.sections.e4 import e4_sections

# 범위 반 변(m): P1 472 · P4 889 · P5 953 · P7 1067 · P8 1117
SHELTERS = [
    place(101, "경로당A", "경로당", -450, tier="village", capacity=30),  # P1 안 → 제외
    place(102, "경로당B", "경로당", 3000, tier="village", capacity=48),  # 밖 → 안전
    place(
        103, "학교A", "학교", 1050, tier="hub", capacity=100
    ),  # P7 안 → 원칙 제외(예외 후보 자격)
    place(104, "학교B", "학교", 4000, tier="hub"),  # 밖 → 안전 · 수용 모름
    place(105, "모텔", "숙박", 2000),  # 후보 아님
]
MOTORWAY = [
    RoadPiece(1, "motorway", "서산영덕고속도로", "30", LineString([(-1500, -900), (1500, -900)])),
    RoadPiece(2, "motorway", "서산영덕고속도로", "30", LineString([(1500, -910), (-1500, -910)])),
]
JUNCTIONS = [place(301, "서의성IC", "IC", -1000, -900), place(302, "안동분기점", "JC", 1000, -900)]
POLICE = [place(201, "의성경찰서", "경찰서", 5000)]


def _run(**kw):
    base = dict(villages=VILLAGES, shelters=SHELTERS, police=POLICE)
    bundles = build_bundles(build_context(inputs(**{**base, **kw})))
    e4 = e4_input(bundles)
    return bundles, e4, (e4_sections(e4) if e4 else None)


def test_shelter_candidates_per_village():
    bundles, e4, sec = _run()
    goesan = bundles["shelters"]["villages"][0]
    assert goesan["village"] == "괴산리"
    # 마을 시설: 가까운 경로당A는 5시간 안이라 빠지고 경로당B만
    # 거점 시설: 학교A(8시간 안) 대신 안전한 학교B
    assert [s["name"] for s in goesan["village_sites"]] == ["경로당B"]
    assert [(s["name"], s["safety"], s["capacity"]) for s in goesan["hub_sites"]] == [
        ("학교B", "safe", None)
    ]
    assert goesan["excluded"] == [
        {"name": "경로당A", "reason": "5시간 안에 불이 닿음"},
        {"name": "학교A", "reason": "8시간 안에 불이 닿음"},
    ]
    assert bundles["shelters"]["counts"]["in_p8"] == 2  # 경로당A · 학교A (모텔은 후보가 아님)
    rows = sec["sections"][0]["blocks"][1]["rows"]
    assert rows[0]["text"].startswith("**괴산리**(지금(이미 닿음), 147명): 먼저 모일 곳 경로당B(")
    assert "학교B(" in rows[0]["text"] and "수용 인원 모름" in rows[0]["text"]


def test_exception_when_no_safe_hub_and_no_options():
    # 안전한 거점이 없으면 8시간 안 학교A를 예외 후보로
    bundles, _, _ = _run(shelters=SHELTERS[:3])
    (hub,) = bundles["shelters"]["villages"][0]["hub_sites"]
    assert (hub["name"], hub["safety"]) == ("학교A", "exception")
    # 쓸 수 있는 곳이 하나도 없으면 no_options
    bundles, e4, _ = _run(shelters=[SHELTERS[0]])
    assert all(r["no_options"] for r in bundles["shelters"]["villages"])
    assert e4["status"] == ["no_options"]


def test_motorway_section_merges_both_directions_and_names_junctions():
    bundles, e4, sec = _run(roads=MOTORWAY, junctions=JUNCTIONS)
    (road,) = bundles["roads"]["sections"]  # 상행 · 하행이 한 구간으로
    assert (road["road"], road["section"]) == ("서산영덕고속도로", "서의성IC~안동분기점")
    assert (road["first_p"], road["from"], road["until"], road["km"]) == (5, "16:50", "17:50", 1.9)
    assert road["police"]["name"] == "의성경찰서"
    assert e4["status"] == ["has_options", "roads_at_risk"]
    assert sec["sections"][1]["blocks"][0]["see"] == "E6"
    # 나들목 자료가 없으면 리 이름으로 쓰고 모름으로 알림
    bundles, _, _ = _run(roads=MOTORWAY)
    assert {"field": "고속도로 나들목 이름", "reason": "자료 없음(리 이름으로 씀)"} in bundles[
        "roads"
    ]["unknown"]
