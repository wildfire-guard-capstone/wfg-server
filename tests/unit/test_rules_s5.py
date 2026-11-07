"""S5: 주 확산 방향 · 다음에 닿는 마을 · 투입 인력 기관(핵심 경우만)."""

from rules_example import AREAS, VILLAGES, inputs, square
from shapely.affinity import translate

from rules.bundles import build_bundles
from rules.context import build_context
from rules.core.places import Deployed
from rules.items.s5 import s5_input
from rules.sections.s5 import s5_sections

CREW = Deployed(crew={"진화대": 81, "소방": 57})


def _run(**kw):
    bundles = build_bundles(build_context(inputs(**kw)))
    s5 = s5_input(bundles)
    return bundles, s5, (s5_sections(s5) if s5 else None)


def test_direction_east_and_next_villages():
    # P1 ~ P8을 동쪽으로 갈수록 더 밀어 둔다(누적은 그대로) → 중심이 동쪽으로 움직임
    ranges = [square(AREAS[0])] + [
        translate(square(ha), xoff=40 * p) for p, ha in enumerate(AREAS) if p
    ]
    bundles, s5, sec = _run(ranges=ranges, villages=VILLAGES, deployed=CREW)
    sp = bundles["spread"]
    assert (sp["dir"], sp["from"]) == ("동", "현재 화선 중심")
    assert [r["name"] for r in sp["next_villages"]] == ["신안리"]
    assert s5["facts"]["front"]["villages"] == ["신안리"]
    assert s5["finding"].startswith("투입 인력 138명 · 주 확산 방향 동(신안리)")
    assert len(sec["sections"][1]["blocks"]) == 3  # 위험 요소 · 철수 조건 · 통신 상태


def test_same_center_direction_unknown_and_zero_crew_no_item():
    bundles, s5, _ = _run(villages=VILLAGES)  # 기본 범위는 같은 중심 · 보고 전
    assert bundles["spread"]["dir"] is None
    assert s5["facts"]["crew_total"] is None  # 보고 전이어도 S5는 만듦
    assert _run(deployed=Deployed(crew={}))[1] is None  # 보고된 인력 0
