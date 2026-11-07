import pytest

from rules.core.commander import (
    Handover,
    Level,
    Reason,
    ReasonKind,
    decide_commander,
    decide_handover,
    head_title,
)

GB, US, AD = "경상북도", "의성군", "안동시"


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


@pytest.mark.parametrize(
    ("reader", "now", "next_", "expected"),
    [
        ("의성군수", "의성군수", "경상북도지사", Handover.PREPARE),
        ("의성군수", "경상북도지사", "경상북도지사", Handover.REVIEW),
        ("의성군수", "의성군수", "의성군수", Handover.NONE),
    ],
)
def test_handover(reader, now, next_, expected):
    assert decide_handover(reader, now, next_) is expected
