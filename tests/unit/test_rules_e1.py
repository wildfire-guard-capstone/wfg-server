"""E1: 마을이 처음 닿는 P → 구간별 묶음 → E1 입력 → 섹션(핵심 경우만)."""

import pytest
from rules_example import WORLD, inputs, village
from shapely.geometry import box

from rules.bundles import build_bundles
from rules.context import build_context
from rules.core.regions import Region
from rules.items.e1 import e1_input
from rules.sections.e1 import e1_sections

# 범위 반 변(m): P0 348 · P1 472 · P2 716 · P5 953 · P8 1117
VILLAGES = [
    village(1, "괴산리", "안평면", 300, 400),  # P0에 걸침 → 이미 닿음
    village(2, "신월리", "안평면", -400, -300),  # P0
    village(3, "신안리", "안평면", 400, 460),  # P1
    village(4, "철파리", "의성읍", 700, 800),  # P2
    village(5, "정자리", "옥산면", 1100, 1200),  # P8 → 잠재 위험구역
    village(6, "먼리", "옥산면", 1200, 1300),  # P8 밖 → 빠짐
]


def _run(**kw):
    bundles = build_bundles(build_context(inputs(**kw)))
    e1 = e1_input(bundles)
    return bundles, e1, (e1_sections(e1, bundles) if e1 else None)


def test_villages_grouped_by_zone_with_time_window():
    bundles, e1, sec = _run(villages=VILLAGES)
    v = bundles["villages"]
    assert [r["name"] for r in v["burning"]] == ["괴산리", "신월리"]
    assert [r["name"] for r in v["p5"]] == ["신안리", "철파리"]
    assert [r["name"] for r in v["p8"]] == ["정자리"]
    sinan = v["p5"][0]
    assert (sinan["first_p"], sinan["from"], sinan["until"]) == (1, "12:50", "13:50")
    assert v["burning"][0]["until"] == "지금"
    assert v["totals"]["within_p5"] == {"count": 4, "population": 400}
    # E1: 판정 없이 고르기
    assert e1["status"] == "risk_zone"
    assert e1["finding"] == (
        "위험구역 4곳: 안평면 괴산리(이미 닿음)·신월리(이미 닿음)·신안리, 의성읍 철파리"
        " / 잠재 위험구역 1곳: 옥산면 정자리"
    )
    rows = sec["sections"][0]["blocks"][1]["rows"]
    assert rows[1]["text"] == "**위험구역(5시간 안)**: 안평면 신안리, 의성읍 철파리"
    assert rows[2]["text"] == "**잠재 위험구역(5~8시간)**: 옥산면 정자리"


def test_only_potential_zone_and_no_villages():
    _, e1, _ = _run(villages=[VILLAGES[4]])
    assert (e1["status"], e1["finding"]) == (
        "potential_only",
        "위험구역 없음 / 잠재 위험구역 1곳: 옥산면 정자리",
    )
    assert _run(villages=[VILLAGES[5]])[1] is None


def test_county_without_village_data_is_an_error():
    # 안동시가 P8에 걸쳤는데 리 자료가 없음 → 빠진 채로 판정하지 않고 멈춤(자료는 나중에 채움)
    andong = Region("안동시", box(1100, -5000, 5000, 5000))
    with pytest.raises(ValueError, match="안동시"):
        build_context(inputs(villages=VILLAGES, sigungu=[Region("의성군", WORLD), andong]))
