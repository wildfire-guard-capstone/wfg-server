# ruff: noqa: E501
"""상황 깃발(situation) → 항목별 작성 지침(guides): 불이 크면 · 작으면 · 밤이면 · 도지사면 켜지는 줄, 덧붙일 주제, 스키마."""

import pytest

from agent import guides, prompt, situation
from agent.generate import Agent, assemble


def bundles(
    now_ha=48.5,
    slices=None,
    stage_now="1단계",
    stage_p5="2단계",
    cm=("의성군수", "경상북도지사"),
    night=False,
    sunset_zone="p8",
):
    slices = (
        slices
        if slices is not None
        else [{"p": 0, "ha": now_ha}, {"p": 2, "ha": 205, "until": "14:50"}, {"p": 5, "ha": 301.5}]
    )
    return {
        "area": {"now_ha": now_ha, "slices": slices},
        "stage": {
            "now": {"stage": stage_now},
            "p5": {"stage": stage_p5},
            "change": {"type": "new"},
        },
        "commander": {"now": {"title": cm[0]}, "next": {"title": cm[1]}},
        "time": {"sun": {"sunset": {"at": "18:38", "zone": sunset_zone}, "now_is_night": night}},
        "villages": {
            "burning": [],
            "p5": [{"name": "신안리", "sigungu": "의성군", "night": False}],
            "p8": [],
        },
        "weather": {"now": {"ms": 3.4}, "max": {"ms": 7.2}},
        "mobilization": {"home": "의성군"},
    }


COMMON = {"reader": "의성군수", "incident": {"sigungu": "의성군"}}
SMALL = situation.flags(
    bundles(
        now_ha=3.0,
        slices=[{"p": 0, "ha": 3.0}, {"p": 5, "ha": 12.0}],
        stage_now="초기대응",
        stage_p5="초기대응",
        cm=("의성군수", "의성군수"),
        sunset_zone=None,
    ),
    [],
    COMMON,
)
GROWING = situation.flags(bundles(), [], COMMON)
LARGE_UPPER = situation.flags(
    bundles(
        now_ha=183.4,
        slices=[{"p": 0, "ha": 183.4}, {"p": 5, "ha": 723}],
        stage_now="2단계",
        stage_p5="2단계",
        cm=("경상북도지사", "경상북도지사"),
    ),
    [],
    {"reader": "경상북도지사"},
)


def test_flags_size_and_escalation():
    assert (
        SMALL["small"]
        and not SMALL["large_soon"]
        and not SMALL["escalation"]
        and not SMALL["commander_change"]
    )
    assert (
        GROWING["large_soon"]
        and GROWING["large_at"] == "14:50"
        and GROWING["escalation"]
        and GROWING["commander_change"]
    )
    assert (
        LARGE_UPPER["large_now"]
        and LARGE_UPPER["reader_upper"]
        and not LARGE_UPPER["commander_change"]
    )
    assert "14:50 무렵 대형산불 기준 100 ha" in situation.summary(GROWING)


@pytest.mark.parametrize("flags", [SMALL, GROWING, LARGE_UPPER])
def test_every_guide_condition_uses_known_flags(flags):
    for iid in guides.GUIDES:  # 모르는 깃발이면 KeyError
        guides.active(iid, flags)
        assert all(ln.kind in ("do", "extra", "avoid") for ln in guides.GUIDES[iid])
        assert all(ln.topic for ln in guides.GUIDES[iid] if ln.kind == "extra")


def test_holds():
    f = {"a": True, "b": False}
    assert guides.holds("", f) and guides.holds("a", f) and not guides.holds("b", f)
    assert guides.holds("not b", f) and guides.holds("b or a", f) and not guides.holds("a and b", f)
    with pytest.raises(KeyError):
        guides.holds("typo", f)


def test_small_fire_avoids_large_fire_actions():
    s1 = guides.render("S1", SMALL)
    assert (
        "대형산불 · 지휘권 이양 · 격상을 가정해 쓰지 않습니다" in s1 and "[5시간 뒤 단계]" not in s1
    )
    assert guides.extra_topics("S6", SMALL) == [] and guides.extra_topics("S2", SMALL) == []
    assert "통합지휘본부 · 재난안전대책본부 가동을 쓰지 않습니다" in guides.render("S6", SMALL)


def test_growing_fire_waits_for_threshold():
    s1 = guides.render("S1", GROWING)
    assert "[5시간 뒤 단계]" in s1 and "지금 인계하라고 쓰지 않습니다" in s1
    assert guides.extra_topics("S6", GROWING) == ["통합지휘본부 운영"]
    assert guides.extra_topics("S4", GROWING) == []  # 밤에 닿는 마을이 없음
    assert "일몰 전까지 공중 진화를 집중" in guides.render("S4", GROWING)


def test_large_fire_upper_reader():
    assert guides.extra_topics("S6", LARGE_UPPER) == ["통합지휘본부 운영", "재난안전대책본부"]
    assert "시장 · 군수에게 진화 인력 안전 지시를 돌리지 않습니다" in guides.render(
        "S5", LARGE_UPPER
    )
    assert "지휘권 인계 · 통보를 쓰지 않습니다" not in guides.render("S1", LARGE_UPPER)


def test_schema_opens_only_active_extras():
    p = prompt.schema(["X"], ["대책회의"], [])["properties"]["proposals"]
    assert (
        p["minItems"] == 1
        and p["maxItems"] == 1
        and p["items"]["properties"]["topic"]["enum"] == ["대책회의"]
    )
    p = prompt.schema(["X"], ["대책회의"], ["통합지휘본부 운영"])["properties"]["proposals"]
    assert p["maxItems"] == 2 and p["items"]["properties"]["topic"]["enum"] == [
        "대책회의",
        "통합지휘본부 운영",
    ]
    p = prompt.schema(["X"], ["대책회의"])["properties"][
        "proposals"
    ]  # 지침 없이: 예전처럼 그 밖의 제안
    assert p["items"]["properties"]["topic"]["enum"] == ["대책회의", "그 밖의 제안"]


ITEM = {
    "id": "S6",
    "name": "유관기관 동원·협조 요청",
    "finding": "다른 기관에 요청할 일 1건",
    "facts": {"request_count": 1},
    "sections": [
        {
            "title": "",
            "blocks": [{"type": "action", "id": "S6-a1", "topic": "대책회의 · 기관별 임무"}],
        }
    ],
}
CHUNK = {
    "chunk_id": "SM-p073-s01",
    "doc_id": "SM",
    "doc_name": "표준매뉴얼",
    "page": "73",
    "section": "p.73",
    "text": "산불현장 대책회의를 열어 기관별 임무를 부여한다. 대형산불은 산불현장 통합지휘본부를 구성한다",
}


def test_assemble_extra_topic_gets_own_section():
    props = [
        {
            "topic": "대책회의 · 기관별 임무",
            "category": "즉시",
            "text": "산불현장 대책회의를 열어 아래 기관별로 임무를 부여하십시오.",
            "evidence": ["SM-p073-s01"],
        },
        {
            "topic": "통합지휘본부 운영",
            "category": "대기",
            "text": "100 ha를 넘으면 산불현장 통합지휘본부를 구성하십시오.",
            "evidence": ["SM-p073-s01"],
        },
    ]
    out = assemble(ITEM, props, {"SM-p073-s01": CHUNK}, "ok")
    assert [s["title"] for s in out["sections"]] == ["", "통합지휘본부 운영"]


class _Ev:
    def __init__(self):
        self.queries = []

    def fetch(self, ids):
        return [], list(ids)

    def search(self, query, k=2, exclude=()):
        self.queries.append(query)
        return [CHUNK | {"score": 0.5}]


class _LLM:
    def __init__(self):
        self.seen = []

    def chat(self, messages, schema=None, **kw):
        import json

        self.seen.append((messages, schema))
        return json.dumps(
            {
                "proposals": [
                    {
                        "topic": "대책회의 · 기관별 임무",
                        "category": "즉시",
                        "text": "산불현장 대책회의를 열어 아래 기관별로 임무를 부여하십시오.",
                        "evidence": ["SM-p073-s01"],
                        "quote": "산불현장 대책회의를 열어 기관별 임무를 부여",
                    }
                ]
            },
            ensure_ascii=False,
        )


def test_agent_puts_guide_in_prompt_and_queries():
    ev, llm = _Ev(), _LLM()
    common = {"reader": "의성군수", "situation": GROWING}
    items, logs = Agent(llm, ev, set(), workers=1).run(common, [ITEM])
    user = llm.seen[0][0][1]["content"]
    assert (
        "[이 항목 작성 지침" in user and "통합지휘본부 운영" in user and "이번 상황: 규모" in user
    )
    assert llm.seen[0][1]["properties"]["proposals"]["items"]["properties"]["topic"]["enum"] == [
        "대책회의 · 기관별 임무",
        "통합지휘본부 운영",
    ]
    assert any("통합지휘본부 구성" in q for q in ev.queries)  # 지침 질의로도 검색
    assert items[0]["state"] == "ok"  # 덧붙일 주제는 빠져도 partial이 아니다
    assert logs[0]["extras"] == ["통합지휘본부 운영"]
