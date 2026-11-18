# ruff: noqa: E501
"""성능 평가(eval1118) 원인 분석에서 나온 제품 오류 6건의 시험(11/18).

1) 송출 지역 변화 깃발: change 「same」은 바뀐 것이 아니다
2) 대상 · 시각 짝 검사(C3): 「송전선은 14:50부터」(송전선 13:50 ~ 14:50)를 잡는다
3) 다시 쓰기가 이름 · 값을 지우지 않게: 지시문 · 값 잃음 SOFT · 고르기 점수
4) 문단 꼴 예시의 값 자리(일몰(HH:MM) · 나머지 ○○리)
5) S2 「진화 우선순위에 따라 아래 순서로」
6) E2 대피 완료 확인 지침 = p.76 (3) 마을순찰대 등 인적 수단
"""

import json

from agent import check, guides, judgment, prompt, situation
from agent.generate import Agent, lost_values


# ── 1) 송출 지역 변화 ─────────────────────────────────────────────
def _alert_flags(changes, removed=()):
    areas = [{"emd": f"면{i}", "level": "go", "change": c} for i, c in enumerate(changes)]
    return situation.flags({"alerts": {"areas": areas, "removed": list(removed)}}, [], {})


def test_alerts_changed_only_for_new_up_down():
    assert not _alert_flags(["same", "same"])["alerts_changed"]  # 회차 2 · 4(예전엔 참)
    assert _alert_flags(["same", "new"])["alerts_changed"]  # 회차 3: 의성읍 · 단촌면 새로
    assert _alert_flags(["up"])["alerts_changed"] and _alert_flags(["down"])["alerts_changed"]
    assert not _alert_flags([])["alerts_changed"]


def test_e5_guide_when_areas_unchanged():
    same = guides.render("E5", _alert_flags(["same", "same"]) | {"night_arrival": False})
    assert "송출 지역이 바뀌었으므로" not in same
    assert "「새로 들어왔다 · 단계가 바뀌었다」고 쓰지 않습니다" in same
    new = guides.render("E5", _alert_flags(["new"]) | {"night_arrival": False})
    assert "송출 지역이 바뀌었으므로" in new and "쓰지 않습니다." in new
    assert "새로 들어왔다" not in new
    # 빠진 지역만 있으면(바뀐 것) 「그대로」 줄은 켜지지 않는다
    gone = guides.render(
        "E5", _alert_flags(["same"], [{"emd": "옥산면", "prev": "set"}]) | {"night_arrival": False}
    )
    assert "새로 들어왔다" not in gone and "해제를 알리라" in gone


def test_e5_example_does_not_say_all_new():
    ex = prompt.examples_of("E5")[0]
    assert "새로 들어왔습니다" not in ex["status"]


# ── 2) 대상 · 시각 짝 ──────────────────────────────────────────────
ITEMS = [
    {
        "id": "S2",
        "facts": {
            "life": {
                "villages": [
                    {
                        "name": "신월리",
                        "zone": "burning",
                        "from": None,
                        "by": "지금",
                        "alert_sent_at": "12:40",
                    },
                    {"name": "양곡리", "zone": "p5", "from": "13:50", "by": "14:50"},
                    {"name": "도덕리", "zone": "p5", "from": "14:50", "by": "15:50"},
                    {"name": "박곡리", "zone": "p8", "from": "17:50", "by": "18:50"},
                ],
                "vulnerable": [
                    {
                        "name": "의료법인성제의료재단 성제요양병원",
                        "kind": "요양병원",
                        "village": "용기리",
                        "by": "14:50",
                    }
                ],
            },
            "power": [
                {
                    "name": "154 kV 송전선 괴산리~신안리 구간",
                    "from": "13:50",
                    "arrival": "14:50",
                    "p8_until": "20:50",
                }
            ],
        },
    },
    {
        "id": "E4",
        "facts": {
            "roads": [
                {
                    "road": "서산영덕고속도로",
                    "section": "용기리",
                    "from": "15:50",
                    "until": "16:50",
                },
                {
                    "road": "서산영덕고속도로",
                    "section": "용기리~도덕리",
                    "from": "16:50",
                    "until": "17:50",
                },
            ]
        },
    },
    {
        "id": "E3",
        "facts": {
            "immediate": [
                {
                    "name": "의료법인성제의료재단 성제요양병원",
                    "kind": "요양병원",
                    "village": "용기리",
                    "from": "13:50",
                    "until": "14:50",
                }
            ]
        },
    },
]
TABLE = check.pair_table(ITEMS, "12:50")


def test_pair_table_targets():
    by = {t.label: t for t in TABLE["targets"]}
    power = by["154 kV 송전선 괴산리~신안리 구간"]
    assert power.kind == "power" and power.starts == {"13:50"} and power.ends == {"14:50", "20:50"}
    assert by["신월리"].starts == {"12:50"} and by["신월리"].others == {
        "12:40"
    }  # 이미 닿음 = 기준 시각
    assert by["서산영덕고속도로 용기리 구간"].kind == "road"
    rest_home = by["의료법인성제의료재단 성제요양병원"]
    assert rest_home.kind == "facility" and "성제요양병원" in rest_home.aliases
    assert rest_home.starts == {"13:50"} and rest_home.ends == {"14:50"}  # S2 · E3을 합침


def test_pair_errors_catch_eval1118_s2():
    """회차 1 S2(C3): 송전선 시간대 13:50 ~ 14:50인데 14:50을 시작 · 기한으로 씀."""
    judg = "송전선은 14:50부터 불이 닿을 수 있으므로 그 전에 조치가 필요하다."
    prop = "154 kV 송전선 괴산리~신안리 구간에 불이 닿을 수 있는 14:50 전에 한국전력공사에 전류 차단과 우회선로 확보를 요청하십시오."
    for text in (judg, prop):
        (err,) = check.pair_errors(text, TABLE)
        assert "14:50은 「154 kV 송전선 괴산리~신안리 구간」의 시작 시각이 아닙니다" in err
        assert "13:50 ~ 14:50 · 20:50" in err
    ok = "154 kV 송전선 괴산리~신안리 구간에 불이 닿을 수 있는 13:50 전에 한국전력공사에 전류 차단을 요청하십시오."
    assert check.pair_errors(ok, TABLE) == []
    assert check.pair_errors("송전선은 20:50까지 2.3 km로 늘어납니다.", TABLE) == []


def test_pair_errors_ranges_and_lists():
    ok = [
        "불은 이미 신월리에 닿았고, 13:50~14:50에 양곡리, 14:50~15:50에 도덕리에 닿을 수 있습니다.",
        "양곡리(13:50 ~ 14:50), 도덕리(14:50 ~ 15:50) 주민을 대피시키십시오.",
        "양곡리 · 도덕리 순으로 13:50부터 15:50 사이에 불이 닿을 수 있습니다.",  # 모아 쓴 범위
        "신월리에는 12:40에 대피 문자가 나갔습니다.",  # 대상에 붙은 다른 시각
        "박곡리는 일몰(18:38) 전에 사전대피시키십시오.",  # 일몰 시각은 보지 않음
        "15:50 서북서 방향 최대 풍속과 양곡리 쪽 확산을 알리십시오.",  # 기상 시각
        "서산영덕고속도로 용기리 구간에는 15:50~16:50에 불이 닿을 수 있습니다.",  # 마을 용기리가 아니라 도로 구간
        "용기리~도덕리 구간에는 16:50~17:50에 불이 닿을 수 있습니다.",  # 도로 이름 없이 구간만
        "성제요양병원은 13:50 전에 우선 대피시키십시오.",  # 시설 짧은 이름
    ]
    for s in ok:
        assert check.pair_errors(s, TABLE) == [], s
    bad = {
        "13:50~14:50에 도덕리에 닿을 수 있습니다.": "「도덕리」의 시간대가 아닙니다",
        "서산영덕고속도로 용기리 구간(16:50~16:50)에 교통통제를 요청하십시오.": "시간대가 아닙니다",
        "17:50 이후 양곡리에 불이 닿을 수 있습니다.": "시작 시각이 아닙니다",
        "성제요양병원 거주자 이송을 14:50 전에 시작하도록 지시하십시오.": "시작 시각이 아닙니다",
    }
    for s, msg in bad.items():
        errs = check.pair_errors(s, TABLE)
        assert errs and msg in errs[0], s
    assert (
        check.pair_errors("송전선은 14:50부터 닿습니다.", None) == []
    )  # 표가 없으면(C1 · C2) 보지 않음


def test_pair_errors_remove_fact_sentence_and_fail_proposal():
    allowed = check.allowed_from(ITEMS)
    facts, errs, removed = check.check_facts(
        "S2",
        {
            "situation": "송전선에는 13:50부터 불이 닿을 수 있습니다. 송전선은 14:50부터 불이 닿을 수 있습니다.",
            "status": "",
            "judgment": "",
        },
        allowed,
        "",
        set(),
        pairs=TABLE,
    )
    assert facts["situation"] == "송전선에는 13:50부터 불이 닿을 수 있습니다."
    assert len(removed) == 1 and "시작 시각이 아닙니다" in errs[0]
    p = {
        "category": "요청",
        "text": "한국전력공사에 14:50 전에 송전선 전류 차단을 요청하십시오.",
        "evidence": ["E1"],
    }
    out = check.check("S2", [p], {"E1"}, allowed, "", set(), pairs=TABLE)
    assert any("시작 시각이 아닙니다" in e for e in out[0])
    assert check.check("S2", [p], {"E1"}, allowed, "", set()) == {}  # pairs 없으면 예전과 같다


# ── 3) 다시 쓰기 · 고르기 ──────────────────────────────────────────
S3 = {
    "id": "S3",
    "name": "진화자원 투입·동원",
    "status": "ok",
    "finding": "응원 기지 3곳",
    "facts": {
        "bases": [
            {"name": "울진산림항공관리소"},
            {"name": "안동시 임차헬기"},
            {"name": "예천군 임차헬기"},
        ]
    },
    "withheld": [],
    "do_not_state": [],
    "targets": [],
    "rule_basis": [],
    "anchor_basis": [],
    "sections": [
        {"title": "1. 투입", "blocks": [{"type": "action", "id": "S3-a1", "topic": "투입 자원"}]}
    ],
}
CHUNK = {
    "chunk_id": "SM-p079-s01",
    "doc_id": "SM",
    "doc_name": "표준매뉴얼",
    "page": "79",
    "section": "p.79",
    "text": "진화자원을 단계적으로 투입한다",
}


class _Evidence:
    def fetch(self, ids):
        return [], list(ids)

    def search(self, query, k=2, exclude=()):
        return [CHUNK | {"score": 0.7}]


class _LLM:
    def __init__(self, texts):
        self.texts, self.seen = list(texts), []

    def chat(self, messages, schema=None, **kw):
        self.seen.append(messages)
        text = self.texts.pop(0)
        return json.dumps(
            {
                "situation": "응원 기지는 3곳입니다.",
                "status": "",
                "judgment": "",
                "proposals": [
                    {
                        "topic": "투입 자원",
                        "category": "대기",
                        "text": text,
                        "evidence": ["SM-p079-s01"],
                        "quote": "진화자원을 단계적으로 투입한다",
                    }
                ],
            },
            ensure_ascii=False,
        )


NAMED = "울진산림항공관리소 · 안동시 임차헬기 · 예천군 임차헬기에 응원 요청을 준비하십시오."


def test_rewrite_that_drops_names_is_retried_and_loses():
    """회차 4 S3: 첫 시도는 이름 3곳 + 근거 없는 19:20 → 다시 쓴 글이 이름을 지움 → 한 번 더 → 이름 있는 글을 고름."""
    llm = _LLM(
        [
            "울진산림항공관리소 · 안동시 임차헬기 · 예천군 임차헬기에 19:20까지 응원 요청을 준비하십시오.",
            "응원 기지에 응원 요청을 준비하십시오.",
            NAMED,
        ]
    )
    items, logs = Agent(llm, _Evidence(), set(), workers=1).run({"reader": "의성군수"}, [S3])
    assert len(llm.seen) == 3
    assert [p["text"] for p in items[0]["proposals"]] == [NAMED]
    assert logs[0]["values_lost"][0]["attempt"] == 2
    retry2 = llm.seen[2][-1]["content"]
    assert "앞서 쓴 이름 · 값(" in retry2 and "울진산림항공관리소" in retry2
    retry1 = llm.seen[1][-1]["content"]
    assert "직전 글 그대로 두고" in retry1 and "묶어 부르는 말로 바꾸거나 지우지 마십시오" in retry1
    assert [a["mentions"] for a in logs[0]["attempts"]][1:] == [1, 4]  # 「3곳」 + 이름 3


def test_rewrite_keeping_names_is_not_retried():
    llm = _LLM(
        [
            "울진산림항공관리소 · 안동시 임차헬기 · 예천군 임차헬기에 19:20까지 응원 요청을 준비하십시오.",
            NAMED,
        ]
    )
    items, logs = Agent(llm, _Evidence(), set(), workers=1).run({"reader": "의성군수"}, [S3])
    assert len(llm.seen) == 2 and "values_lost" not in logs[0]
    assert items[0]["state"] == "ok"


def test_lost_values_threshold():
    allowed = check.allowed_from(S3)
    names = check.data_names(S3["facts"])
    hist = {"투입 자원": check.data_values(NAMED, allowed, names)}
    assert lost_values(
        [{"topic": "투입 자원", "text": "응원 기지에 요청하십시오."}], hist, allowed, names
    ) == {0: ["안동시 임차헬기", "예천군 임차헬기", "울진산림항공관리소"]}
    two = "울진산림항공관리소 · 안동시 임차헬기에 요청하십시오."  # 하나만 잃음 → 다시 쓰지 않음
    assert lost_values([{"topic": "투입 자원", "text": two}], hist, allowed, names) == {}


def test_data_values_counts_names_times_numbers():
    allowed = check.allowed_from({"t": "13:50", "n": 245, "x": "양곡리"})
    names = check.data_names({"name": "의료법인성제의료재단 성제요양병원", "villages": ["양곡리"]})
    vals = check.data_values("13:50까지 양곡리 245명과 성제요양병원, 19:20", allowed, names)
    assert vals == {"시각 13:50", "수 245", "수 13", "양곡리", "성제요양병원"}


# ── 4) 5) 6) 예시 · 지침 ──────────────────────────────────────────
def test_examples_have_value_slots():
    s4 = prompt.examples_of("S4")[0]["proposals"]
    assert "일몰(HH:MM) 전까지는" in s4 and "일몰(HH:MM) 뒤에는" in s4 and "일몰 전까지" not in s4
    assert "나머지 ○○리 · ○○리 · ○○리 주민의" in prompt.examples_of("E4")[0]["proposals"]
    assert "넘으면(HH:MM 무렵) 즉시" in prompt.examples_of("S1")[0]["proposals"]
    assert prompt.STYLE["S4"][0]["raw"]["proposals"].startswith("일몰(18:38) 전까지는")


def test_s2_points_to_list_below():
    flags = _alert_flags([]) | {
        "power_lines": False,
        "heritage": False,
        "large_now": False,
        "large_soon": False,
    }
    text = guides.render("S2", flags)
    assert "「진화 우선순위에 따라 아래 순서로 방어하고 보호하십시오」로 씁니다" in text
    assert "범주 이름을 늘어놓지 않습니다" in text
    assert prompt.examples_of("S2")[0]["proposals"].startswith(
        "진화 우선순위에 따라 아래 순서로 방어하고 보호하십시오."
    )


def test_e2_unconfirmed_guide_points_to_p76_human_means():
    flags = _alert_flags([]) | {
        "evac_unconfirmed": True,
        "night_arrival": False,
        "other_sigungu": False,
    }
    (line,) = [ln for ln in guides.active("E2", flags) if ln.when == "evac_unconfirmed"]
    assert "이장 · 마을순찰대 같은 인적 수단" in line.text and "대피가 끝났는지 확인" in line.text
    assert "근거에서 찾아" not in line.text  # 코퍼스에 없는 문장을 찾으라고 하지 않는다
    assert line.q.startswith("상황이 긴급한 경우 마을순찰대 등 인적 수단")
    text = guides.render("E2", flags)
    assert "근거는 발췌의 「마을순찰대" in text
    assert "근거는 발췌의" not in prompt.no_evidence(text)  # C1(근거 없음)에서는 근거 구절을 지운다
    off = guides.render("E2", flags | {"evac_unconfirmed": False})
    assert "마을순찰대" not in off


def test_c1_prompt_has_no_evidence_phrase_for_e2():
    item = {
        "id": "E2",
        "name": "대피",
        "finding": "",
        "facts": {},
        "withheld": [],
        "do_not_state": [],
        "sections": [],
    }
    flags = _alert_flags([]) | {
        "evac_unconfirmed": True,
        "night_arrival": False,
        "other_sigungu": False,
    }
    j = judgment.judgment_of(item)
    user = prompt.messages(
        {"reader": "의성군수"}, j, [], guide=guides.render("E2", flags), condition="C1"
    )[1]["content"]
    assert "인적 수단" in user and "근거는 발췌의" not in user
