"""S1: 입력 → 공통 결과 → 엔진 묶음 → S1 입력 → 섹션을 12:50 예시로 끝까지 돌린다."""

from rules_example import BASE, inputs

from rules.bundles import build_bundles
from rules.context import build_context
from rules.core.convert import p_until, weekday
from rules.core.stage import Stage
from rules.items.s1 import s1_input
from rules.sections.s1 import s1_sections


def _inputs(prev_stage=None, wind_now=3.4, reader="의성군수"):
    wind = {0: wind_now, **{h: 2.0 for h in range(1, 9)}}
    return inputs(prev_stage=prev_stage, wind_ms=wind, reader=reader)


def _run(**kw):
    bundles = build_bundles(build_context(_inputs(**kw)))
    s1 = s1_input(bundles)
    return bundles, s1, s1_sections(s1, bundles)


def test_convert_now_and_weekday():
    assert p_until(BASE, 0) == "지금"
    assert p_until(BASE, 2) == "14:50"
    assert weekday(BASE) == "토요일"


def test_example_1250_end_to_end():
    bundles, s1, sec = _run()
    stage = bundles["stage"]
    # 현재: 세 요소 모두 1단계 · P5: 면적으로 2단계 → 추세 up · 첫 제안서
    assert stage["now"]["decided_by"] == ["area", "wind", "facility"]
    assert (stage["p5"]["stage"], stage["p5"]["decided_by"]) == ("2단계", ["area"])
    assert (stage["trend"], stage["change"]["type"]) == ("up", "new")
    # 100 ha를 P2(14:50)에 처음 넘고, 1,000 ha는 P8 안에서 안 넘음
    assert bundles["area"]["first_over"] == [{"ha": 100, "p": 2, "until": "14:50", "area_ha": 205}]
    # 지휘권자: 지금 군수 · 곧 도지사(대형산불). 할 일 이름(handover)은 없음
    commander = bundles["commander"]
    assert (commander["now"]["title"], commander["next"]["title"]) == ("의성군수", "경상북도지사")
    assert commander["next"]["reasons"][0]["text"] == "피해면적 100 ha 이상 대형산불"
    assert "handover" not in commander
    # S1 입력: 상황 이름 바꾸기 · 요소 키 → 글
    assert s1["status"] == "escalation_expected"
    assert s1["facts"]["now"]["decided_by"] == [
        "피해면적 48.5 ha",
        "평균풍속 3.4 m/s",
        "시설피해 우려 최소 2동",
    ]
    assert s1["finding"].endswith(
        "→ 5시간 안에 2단계 기준 도달 예상 · 지휘권자 의성군수 → 경상북도지사"
    )
    # 섹션: a1 · a2(면적 흐름 포함) · a3(대형산불 근거)
    a1, a2 = sec["sections"][0]["blocks"]
    (a3,) = sec["sections"][1]["blocks"]
    assert (a1["topic"], a2["topic"], a3["topic"]) == (
        "현재 기준 단계",
        "5시간 뒤 단계",
        "지휘권자",
    )
    assert a2["use"] == ["p5", "area"]
    assert a3["anchors"] == ["p.72", "p.114", "시행령 제21조①", "시행령 제21조②"]
    assert sec["do_not_state"] == ["진화예상시간"]


def test_same_stage_as_before_has_no_a2_and_topics_do_not_change():
    # 바람이 약해 현재도 면적 · 시설로 1단계, 직전도 1단계 · P5는 2단계라 a2는 그대로
    _, s1, sec = _run(prev_stage=Stage.STAGE1, wind_now=1.0)
    assert s1["facts"]["change"]["type"] == "same"
    assert sec["sections"][0]["blocks"][0]["topic"] == "현재 기준 단계"


def test_reader_is_next_commander_gets_same_facts():
    # 도지사가 읽어도 엔진은 세 이름만 준다 → 인계 / 인수는 LLM이 정함
    # (옛 handover는 이 경우 「지금 인계」로 틀렸음)
    _, s1, _ = _run(reader="경상북도지사")
    commander = s1["facts"]["commander"]
    assert (commander["reader"], commander["now"]["title"], commander["next"]["title"]) == (
        "경상북도지사",
        "의성군수",
        "경상북도지사",
    )
