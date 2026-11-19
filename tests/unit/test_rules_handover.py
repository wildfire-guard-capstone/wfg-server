"""지휘권자가 올라가는 구간(11/19 평가 R1): 입력 → 공통 결과 → 묶음 → LLM 공통 입력 · S1.

「곧(P5)」 지휘권자만 넘기면 LLM이 「5시간 뒤 이관」으로 썼다. 기준을 넘는 구간의 시각 · 이유를
함께 넘기는지, 기준을 안 넘는 경우(그대로 · 이미 넘음 · P6 뒤에 넘음)는 빈 목록인지 본다.
"""

from rules_example import inputs, square, village
from shapely.geometry import box

from rules.bundles import build_bundles
from rules.context import build_context
from rules.core.regions import Region
from rules.items.s1 import s1_input
from rules.llm_input import build_common
from rules.sections.s1 import s1_sections

SMALL = [5, 10, 20, 30, 40, 50, 60, 70, 80]  # P8까지 100 ha 안 넘음 · 반 변 P3 274 m · P4 316 m
HOME = village(99, "먼리", "옥산면", 5000, 5100)


def _run(areas=None, **kw):
    if areas is not None:
        kw["ranges"] = [square(ha) for ha in areas]
    bundles = build_bundles(build_context(inputs(**kw)))
    return bundles, build_common(bundles, {"name": "산불"})["commander"]


def _split(name_a, name_b, x):
    """x에서 동서로 나뉜 두 지역(서쪽 = name_a)."""
    return [Region(name_a, box(-1e5, -1e5, x, 1e5)), Region(name_b, box(x, -1e5, 1e5, 1e5))]


def test_stays_same_has_no_steps():
    # ① P8까지 100 ha 안 넘고 한 군 안 → 지금 = 곧 = 군수, 넘어가는 구간 없음
    bundles, common = _run(SMALL)
    assert common == {"now": "의성군수", "next": "의성군수", "steps": []}
    assert bundles["commander"]["steps"] == []


def test_already_large_now_has_no_steps():
    # ② 지금 이미 100 ha 넘음 → 지금부터 도지사, 1,000 ha는 안 넘어 더 올라가지 않음
    bundles, common = _run([150, 200, 250, 300, 350, 400, 450, 500, 550])
    assert common == {"now": "경상북도지사", "next": "경상북도지사", "steps": []}
    assert bundles["commander"]["now"]["reasons"][0]["text"] == "피해면적 100 ha 이상 대형산불"


def test_area_crossing_gives_slice_time():
    # ③ 12:50 예시: P2(13:50 ~ 14:50)에 205 ha → 그 구간에 도지사
    bundles, common = _run()
    (step,) = bundles["commander"]["steps"]
    assert (step["p"], step["from"], step["until"], step["title"]) == (
        2,
        "13:50",
        "14:50",
        "경상북도지사",
    )
    assert common["steps"] == [
        {"title": "경상북도지사", "at": "13:50 ~ 14:50", "why": "피해면적 100 ha 이상 대형산불"}
    ]


def test_crossing_after_p5_is_not_a_handover():
    # ④ P7에 처음 100 ha → 곧(P5)은 군수 그대로라 넘어가는 구간 없음. 면적 흐름에는 남음
    bundles, common = _run([10, 20, 30, 40, 50, 60, 80, 120, 150])
    assert common == {"now": "의성군수", "next": "의성군수", "steps": []}
    assert [(o["ha"], o["p"]) for o in bundles["area"]["first_over"]] == [(100, 7)]


def test_span_without_area_gives_slice_time():
    # ⑤ 면적은 안 넘는데 P4(반 변 316 m)에 x = 300 m 동쪽 안동시에 걸침 → P4에 도지사
    villages = [HOME, village(98, "먼동리", "일직면", 5000, 5100, sigungu="안동시")]
    bundles, common = _run(SMALL, sigungu=_split("의성군", "안동시", 300), villages=villages)
    assert common["steps"] == [
        {
            "title": "경상북도지사",
            "at": "15:50 ~ 16:50",
            "why": "2개 이상 시 · 군 · 구에 걸침(의성군 · 안동시)",
        }
    ]
    assert bundles["commander"]["next"]["title"] == "경상북도지사"


def test_two_levels_keep_middle_commander():
    # ⑥ P2 도지사(205 ha) → P5(반 변 953 m)에 x = 900 m 동쪽 충청북도에 걸쳐 산림청장
    sido = _split("경상북도", "충청북도", 900)
    sigungu = _split("의성군", "단양군", 900)
    villages = [HOME, village(97, "먼단리", "영춘면", 5000, 5100, sigungu="단양군")]
    bundles, common = _run(sido=sido, sigungu=sigungu, villages=villages)
    assert [(s["title"], s["at"]) for s in common["steps"]] == [
        ("경상북도지사", "13:50 ~ 14:50"),
        ("산림청장", "16:50 ~ 17:50"),
    ]
    # 곧(P5) 이유는 2개 시·도뿐이지만, 중간 도지사의 면적 근거도 묶음 · S1 근거에 남음
    assert [r["kind"] for r in bundles["commander"]["next"]["reasons"]] == ["span_sido"]
    assert "SM-p114" in bundles["commander"]["basis"]
    s1 = s1_input(bundles)
    assert s1["finding"].endswith(
        "지휘권자 의성군수 → 경상북도지사(13:50 ~ 14:50) → 산림청장(16:50 ~ 17:50)"
    )
    (a3,) = s1_sections(s1, bundles)["sections"][1]["blocks"]
    assert {"p.114", "법 제34조②"} <= set(a3["anchors"])
