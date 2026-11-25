"""E3: 불에 가까운 취약시설의 구간 · 시각, E3는 고르기만 하는지(핵심 경우만)."""

from rules_example import VILLAGES, VULNERABLE, inputs, place

from rules.bundles import build_bundles
from rules.context import build_context
from rules.items.e3 import e3_input
from rules.sections.e3 import e3_sections


def _run(**kw):
    bundles = build_bundles(build_context(inputs(villages=VILLAGES, **kw)))
    e3 = e3_input(bundles)
    return bundles, e3, (e3_sections(e3) if e3 else None)


def test_facilities_near_fire_and_far_is_left_out():
    later = place(63, "산너머요양원", "노인요양원", 1600)  # P5(953 m)에서 647 m · P8에서 483 m
    far = place(64, "아주먼요양원", "노인요양원", 2200)  # P8(1,117 m)에서 1 km 밖
    bundles, e3, sec = _run(vulnerable=[*VULNERABLE, later, far])
    v = bundles["vulnerable"]
    # 철파리(P2) 3곳 · P5에서 0.5 km 안 2곳(가까워지기만) · P8에서 1 km 안 1곳. 1 km 밖은 빠짐
    assert [r["name"] for r in v["in_range"]] == [
        "의료법인 해안의료재단 해안1병원",
        "의성군공립요양병원",
        "의성행복한노인복지센터",
        "먼요양원",
        "정자요양원",
        "산너머요양원",
    ]
    near = v["in_range"][3]
    assert (near["touched"], near["from"], near["until"]) == (False, None, "17:50")
    assert near["approach_km"] == 0.3
    hospital = v["in_range"][1]
    assert (hospital["zone"], hospital["from"], hospital["until"]) == ("p5", "13:50", "14:50")
    assert "ministry" not in hospital  # 소관 부처는 매뉴얼 기관별 임무 → LLM · 요청 표 틀
    assert v["counts"] == {"within_p5": 5, "p8": 1, "daytime_only": 1}
    # E3: 나누기만, 할 일(이송 · 사전대피)은 넣지 않음
    assert e3["facts"]["weekday"] == "토요일"
    assert e3["finding"].startswith(
        "위험구역 시설 5곳: 의료법인 해안의료재단 해안1병원(정신병원, 철파리)"
    )
    # 섹션: 1. 우선 대피(a1 + 목록 + 낮에만 운영 a2) · 2. 사전대피(a3) · 3. 이송(a4, E6 참조)
    titles = [s["title"] for s in sec["sections"]]
    assert titles == ["1. 우선 대피", "2. 사전대피", "3. 이송"]
    rows = sec["sections"][0]["blocks"][1]["rows"]
    assert rows == [
        {
            "village": "철파리",
            "text": "13:50 ~ 14:50 닿을 수 있음, **철파리**: "
            "의료법인 해안의료재단 해안1병원(정신병원), "
            "의성군공립요양병원(요양병원), 의성행복한노인복지센터(주야간보호)",
        },
        {"village": "먼리", "text": "17:50에 불이 0.3 km까지 접근, **먼리**: 먼요양원(노인요양원)"},
        {
            "village": "정자리",
            "text": "17:50에 불이 0.2 km까지 접근, **정자리**: 정자요양원(노인요양원)",
        },
    ]
    assert sec["sections"][0]["blocks"][2]["kind"] == "check"
    assert sec["sections"][2]["blocks"][0]["see"] == "E6"


def test_unknown_kind_is_reported_and_no_facility_means_no_item():
    _, e3, _ = _run(vulnerable=[place(70, "의성자혜원", None, 750, village_id=4)])
    assert {"field": "의성자혜원 종류", "reason": "자료 없음"} in e3["withheld"]
    assert _run(vulnerable=[])[1] is None
