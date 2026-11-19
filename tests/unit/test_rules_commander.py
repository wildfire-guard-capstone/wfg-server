import pytest

from rules.core.commander import (
    Level,
    Reason,
    ReasonKind,
    decide_commander,
    handover_steps,
    head_title,
)

GB, US, AD = "경상북도", "의성군", "안동시"


def _by_p(areas, sigungu=((US,),) * 6, sido=((GB,),) * 6):
    return [decide_commander(a, s, g) for a, s, g in zip(areas, sido, sigungu, strict=True)]


def test_handover_none_when_threshold_never_crossed():
    # P5까지 100 ha를 안 넘고 한 군 안 → 지휘권자 그대로, 넘어가는 구간 없음
    assert handover_steps(_by_p([5, 10, 20, 40, 60, 90])) == []


def test_handover_none_when_already_over_now():
    # 지금 이미 대형산불(도지사) · 1,000 ha는 안 넘음 → 더 올라가지 않음
    assert handover_steps(_by_p([150, 200, 300, 400, 500, 600])) == []


def test_handover_at_first_slice_over_100_ha():
    # 12:50 예시: P2에 205 ha → P2에서 도지사(그 뒤 구간은 같은 도지사라 다시 세지 않음)
    steps = handover_steps(_by_p([48.5, 89, 205, 262, 316, 363]))
    assert [(p, c.title) for p, c in steps] == [(2, "경상북도지사")]
    assert steps[0][1].reasons == (Reason(ReasonKind.AREA, threshold_ha=100),)


def test_handover_by_span_without_area():
    # 면적은 안 넘어도 P4에 이웃 시·군에 걸치면 그 구간에서 도지사
    sigungu = [(US,)] * 4 + [(US, AD)] * 2
    steps = handover_steps(_by_p([5, 10, 20, 30, 40, 50], sigungu=sigungu))
    assert [(p, c.title, c.reasons[0].kind) for p, c in steps] == [
        (4, "경상북도지사", ReasonKind.SPAN_SIGUNGU)
    ]


def test_handover_two_levels_keeps_both_steps():
    # P2 도지사(면적) → P5 산림청장(2개 시·도). decide_commander(P5)만 보면 중간 도지사가 사라짐
    sido = [(GB,)] * 5 + [(GB, "충청북도")]
    steps = handover_steps(_by_p([48.5, 89, 205, 262, 316, 363], sido=sido))
    assert [(p, c.title) for p, c in steps] == [(2, "경상북도지사"), (5, "산림청장")]


def test_handover_needs_now():
    with pytest.raises(ValueError):
        handover_steps([])


def test_example_now_local_head():
    c = decide_commander(48.5, [GB], [US])
    assert (c.level, c.title, c.reasons) == (Level.LOCAL, "의성군수", ())


def test_example_next_large_fire_goes_to_sido():
    c = decide_commander(363, [GB], [US])
    assert (c.level, c.title) == (Level.SIDO, "경상북도지사")
    assert c.reasons == (Reason(ReasonKind.AREA, threshold_ha=100),)


def test_span_two_sigungu_goes_to_sido():
    c = decide_commander(40, [GB], [US, AD])
    assert (c.level, c.title) == (Level.SIDO, "경상북도지사")
    assert c.reasons == (Reason(ReasonKind.SPAN_SIGUNGU, names=(US, AD)),)


def test_higher_commander_wins_and_keeps_only_its_reasons():
    # 시군구 2개(→ 시·도지사) + 1,200 ha(→ 산림청장)
    c = decide_commander(1200, [GB], [US, AD])
    assert (c.level, c.title) == (Level.NATIONAL, "산림청장")
    assert c.reasons == (Reason(ReasonKind.AREA, threshold_ha=1000),)


def test_two_sido_and_large_area_list_every_reason():
    c = decide_commander(1200, [GB, "충청북도"], [US, "단양군"])
    assert [r.kind for r in c.reasons] == [ReasonKind.SPAN_SIDO, ReasonKind.AREA]


def test_wind_driven_stage2_alone_does_not_move_command():
    # 옛 규칙(대응단계 기준)은 여기서 시·도지사. 11/7 규칙은 면적 · 걸친 지역만 본다
    assert decide_commander(40, [GB], [US]).level is Level.LOCAL


@pytest.mark.parametrize(
    ("region", "head"),
    [
        (US, "의성군수"),
        (AD, "안동시장"),
        ("중구", "중구청장"),
        (GB, "경상북도지사"),
        ("대구광역시", "대구광역시장"),
    ],
)
def test_head_title(region, head):
    assert head_title(region) == head
