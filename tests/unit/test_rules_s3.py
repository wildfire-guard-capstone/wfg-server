"""S3: 동원 범위(J7) · 범위 안 기지 · 투입(상황 보고, 라벨 고정)(핵심 경우만)."""

import pytest
from rules_example import VILLAGES, inputs, place
from shapely.geometry import Point

from rules.bundles import build_bundles
from rules.context import build_context
from rules.core.places import Base, Deployed
from rules.items.s3 import s3_input
from rules.sections.s3 import s3_sections

NEIGHBORS = {"의성군": ["안동시", "구미시", "상주시", "영천시", "예천군", "청송군", "군위군"]}


def _base(id_, name, kind, sigungu, sido, coverage, x, **kw):
    return Base(id_, name, kind, sigungu, sido, coverage, Point(x, 0), **kw)


BASES = [
    _base(
        1,
        "안동산림항공관리소",
        "산림청 헬기",
        "안동시",
        "경상북도",
        ["의성군", "안동시"],
        30_000,
        helicopters=2,
    ),
    _base(
        2,
        "울진산림항공관리소",
        "산림청 헬기",
        "울진군",
        "경상북도",
        ["울진군"],
        95_000,
        helicopters=1,
    ),
    _base(3, "강릉산림항공관리소", "산림청 헬기", "강릉시", "강원특별자치도", ["강릉시"], 150_000),
    _base(4, "의성소방서", "지상 소방", "의성군", "경상북도", ["의성군"], 3_000, vehicles=6),
    _base(5, "포항남부소방서", "지상 소방", "포항시", "경상북도", ["포항시"], 90_000, vehicles=8),
    _base(
        6, "대구중부소방서", "지상 소방", "대구중구", "대구광역시", ["대구중구"], 60_000, vehicles=5
    ),
]
DEPLOYED = Deployed(
    helicopter={"산림청": 5, "지자체": 16, "소방": 2},
    crew={"진화대": 81, "공무원": 13, "소방": 57},
    vehicle={"지휘·진화차": 10, "소방차": 15, "기타": 1},
    reported_at="12:50",
)


def _run(**kw):
    base = dict(
        villages=VILLAGES,
        bases=BASES,
        neighbors=NEIGHBORS,
        deployed=DEPLOYED,
        forest_dept={"의성군": "의성군 산림녹지과"},
        fire_stations=[place(401, "의성소방서", "소방서", 3000, sigungu="의성군")],
    )
    bundles = build_bundles(build_context(inputs(**{**base, **kw})))
    s3 = s3_input(bundles)
    return bundles, s3, s3_sections(s3)


def test_scope_and_bases_now_1_then_2():
    bundles, s3, sec = _run()
    mob = bundles["mobilization"]
    assert mob["now"]["ground"]["extra"]["kind"] == "neighbors"  # 1단계: 인접 시군 50% 이내
    assert mob["p5"]["air"] == {"kind": "national", "km": None}  # 2단계: 전국
    scopes = {b["name"]: (b["first_scope"], b["jurisdiction"]) for b in mob["bases"]}
    assert scopes == {
        "의성소방서": ("now", "관할"),
        "안동산림항공관리소": ("now", "관할"),  # 관할 구역에 의성군
        "포항남부소방서": ("p5", "응원"),  # 인접 시군 아님 → 2단계(경북 전체)에서 들어옴
        "울진산림항공관리소": ("now", "응원"),  # 95 km ≤ 100 km
        "강릉산림항공관리소": ("p5", "응원"),  # 150 km → 2단계(전국)에서 들어옴
    }  # 대구중부소방서(다른 시도)는 어느 범위에도 안 들어옴
    assert [b["name"] for b in s3["facts"]["added_at_p5"]] == [
        "포항남부소방서",
        "강릉산림항공관리소",
    ]
    assert s3["status"] == "scope_expands"
    assert s3["finding"] == (
        "투입: 헬기 23 · 장비 26 · 인력 151 / 현재 1단계 → 5시간 뒤 2단계:"
        " 동원 범위 지상 경상북도 100% · 공중 전국으로 확대"
    )
    crew = bundles["resources"]["crew"]
    assert (crew["orgs"], crew["org_count"]) == (["의성군 산림녹지과", "의성소방서"], 2)
    assert [b["id"] for s in sec["sections"] for b in s["blocks"] if b["type"] == "action"] == [
        "S3-a1",
        "S3-a2",
        "S3-a3",
    ]


def test_before_report_is_unknown_not_zero_and_bad_label_errors():
    bundles, s3, _ = _run(deployed=None)
    assert bundles["resources"]["helicopter"]["total"] is None
    assert s3["finding"].startswith("투입: 상황 보고 전")
    assert {"field": "투입 수", "reason": "상황 보고 전"} in s3["withheld"]
    with pytest.raises(ValueError, match="모르는 라벨"):
        _run(deployed=Deployed(helicopter={"민간": 1}))
