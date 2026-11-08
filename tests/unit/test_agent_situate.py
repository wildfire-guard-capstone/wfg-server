# ruff: noqa: E501
"""판정 값 상황 문장 조립(situate): 규칙 엔진 facts → 문장, 할 일 문장 검사, 조립."""

import json

from agent import check, prompt, situate
from agent.generate import Agent

AREA_R1 = {
    "now_ha": 48.5,
    "slices": [{"p": 2, "until": "14:50", "ha": 119.7}, {"p": 5, "until": "17:50", "ha": 301.5}],
    "first_over": [{"ha": 100, "p": 2, "until": "14:50", "area_ha": 119.7}],
}
S1_R1 = {
    "id": "S1",
    "facts": {
        "now": {
            "stage": "1단계",
            "area_ha": 48.5,
            "wind_ms": 3.4,
            "facility": 1,
            "facility_is_minimum": True,
            "by_area": "1단계",
            "by_wind": "1단계",
            "by_facility": "1단계",
        },
        "p5": {
            "stage": "2단계",
            "area_ha": 301.5,
            "wind_ms": 5.5,
            "facility": 1,
            "facility_is_minimum": True,
            "by_area": "2단계",
            "by_wind": "1단계",
            "by_facility": "1단계",
        },
        "change": {"prev": None, "now": "1단계", "type": "new"},
        "area": AREA_R1,
        "commander": {
            "now": {"title": "의성군수", "reasons": []},
            "next": {
                "title": "경상북도지사",
                "reasons": [{"kind": "area", "ha": 100, "text": "피해면적 100 ha 이상 대형산불"}],
            },
        },
    },
}
S1_R4 = {
    "id": "S1",
    "facts": {
        "now": {
            "stage": "2단계",
            "area_ha": 183.4,
            "wind_ms": 4.9,
            "facility": 8,
            "facility_is_minimum": True,
            "by_area": "2단계",
            "by_wind": "1단계",
            "by_facility": "1단계",
        },
        "change": {"prev": "1단계", "now": "2단계", "type": "up"},
        "commander": {
            "now": {
                "title": "경상북도지사",
                "reasons": [{"kind": "area", "ha": 100, "text": "피해면적 100 ha 이상 대형산불"}],
            },
            "next": {"title": "경상북도지사", "reasons": []},
        },
    },
}
S3_R1 = {
    "id": "S3",
    "facts": {
        "deployed": {
            "helicopter": {
                "total": 23,
                "by": [
                    {"label": "산림청", "n": 5},
                    {"label": "지자체", "n": 16},
                    {"label": "소방", "n": 2},
                    {"label": "군", "n": 0},
                ],
            },
            "crew": {
                "total": 151,
                "by": [
                    {"label": "진화대", "n": 81},
                    {"label": "공무원", "n": 13},
                    {"label": "소방", "n": 57},
                ],
            },
            "vehicle": {
                "total": 26,
                "by": [
                    {"label": "지휘·진화차", "n": 10},
                    {"label": "소방차", "n": 15},
                    {"label": "기타", "n": 1},
                ],
            },
        },
        "scope": {
            "now": {
                "stage": "1단계",
                "ground": {
                    "home": {"name": "의성군", "share": "100%"},
                    "extra": {
                        "kind": "neighbors",
                        "names": ["구미시", "청송군"],
                        "share": "50% 이내",
                    },
                },
                "air": {"kind": "radius", "km": 100},
            },
            "p5": {
                "stage": "2단계",
                "ground": {
                    "home": {"name": "의성군", "share": "100%"},
                    "extra": {"kind": "sido", "names": ["경상북도"], "share": "100%"},
                },
                "air": {"kind": "national", "km": None},
            },
        },
    },
}
S2_R1 = {
    "id": "S2",
    "facts": {
        "houses": {
            "in_p5": [{"village": "신월리", "houses": 1}],
            "in_p5_total": 1,
            "count_basis": "주용도 확인 건물 기준, 최소값",
        },
        "power": [
            {
                "name": "154 kV 송전선 괴산리 구간",
                "zone": "p5",
                "from": "14:50",
                "km_p5": 1.3,
                "km_p8": 1.7,
                "p8_until": "20:50",
                "p8_adds": ["신안리"],
            }
        ],
        "substations": [],
        "temple": [
            {
                "name": "운람사",
                "kind": "전통사찰",
                "emd": "안평면",
                "village": "신안리",
                "zone": "p5",
                "from": "13:50",
                "arrival": "14:50",
            }
        ],
        "heritage": {"total": 46, "in_p8": 0, "items": []},
    },
}


def test_s1_sentences_use_judged_values():
    s = situate.sentences(S1_R1)
    assert (
        s["현재 기준 단계"]
        == "피해면적 48.5 ha, 평균풍속 3.4 m/s, 시설피해 우려 최소 1동으로 세 기준 모두 판정 기준상 확산대응 1단계에 해당합니다."
    )
    assert (
        s["5시간 뒤 단계"]
        == "피해면적이 14:50에 119.7 ha, 17:50에 301.5 ha로 확산대응 2단계 기준 100 ha를 넘을 수 있습니다."
    )
    assert (
        s["지휘권자"]
        == "현재 지휘권자는 의성군수입니다. 피해면적이 14:50 무렵 100 ha를 넘으면 대형산불이 되어 경상북도지사가 통합지휘합니다."
    )


def test_s1_mixed_criteria_not_all_three():
    # tune11: LLM이 「최소 20동」 · 「세 기준 모두」로 씀 → 조립은 기준별 단계를 그대로
    s = situate.sentences(S1_R4)
    assert s["현재 기준 단계"] == (
        "피해면적 183.4 ha는 확산대응 2단계, 평균풍속 4.9 m/s와 시설피해 우려 최소 8동은 확산대응 1단계 기준으로, "
        "판정 기준상 확산대응 2단계에 해당합니다. 직전 회차 확산대응 1단계에서 올랐습니다."
    )
    assert "세 기준 모두" not in s["현재 기준 단계"] and "20동" not in s["현재 기준 단계"]
    assert (
        s["지휘권자"] == "피해면적 100 ha 이상 대형산불이므로 현재 지휘권자는 경상북도지사입니다."
    )
    assert "5시간 뒤 단계" not in s  # p5 없음


def test_s3_scope_keeps_home_county():
    s = situate.sentences(S3_R1)
    assert s["투입 자원"] == (
        "현재 헬기 23대(산림청 5, 지자체 16, 소방 2), 인력 151명(진화대 81, 공무원 13, 소방 57), "
        "장비 26대(지휘·진화차 10, 소방차 15, 기타 1)가 투입되어 있습니다."
    )
    # tune11: 1단계 지상 범위를 「경상북도 100%」로 잘못 씀 → 판정 그대로 의성군 100%
    assert (
        s["현재 단계 동원 범위"]
        == "확산대응 1단계 동원 범위는 지상이 의성군 100%와 인접 시군(구미시, 청송군) 50% 이내, 공중이 발화지 반경 100 km입니다."
    )
    assert (
        s["5시간 뒤 동원 범위"]
        == "5시간 뒤 확산대응 2단계가 되면 동원 범위가 지상은 경상북도 100%, 공중은 전국으로 넓어집니다."
    )


def test_s2_protection_targets():
    s = situate.sentences(S2_R1)
    assert s["주택 보호 배치"] == "5시간 범위 안의 주택은 신월리 최소 1동입니다."
    assert s["송전시설 보호"] == (
        "154 kV 송전선 괴산리 구간에 14:50부터 불이 닿을 수 있고, 5시간 범위 안 길이는 1.3 km입니다. "
        "20:50까지 신안리 구간을 포함해 1.7 km로 늘어납니다."
    )
    assert (
        s["문화유산 이동 · 경비"]
        == "운람사(안평면 신안리, 전통사찰)에 13:50부터 불이 닿을 수 있습니다."
    )
    assert "진화 우선순위" not in s  # 값이 없는 주제는 LLM이 쓴다
    assert situate.sentences(S2_R1, ["주택 보호 배치"]) == {"주택 보호 배치": s["주택 보호 배치"]}


def test_broken_facts_fall_back_to_llm():
    assert situate.sentences({"id": "S1", "facts": {"now": {"stage": "1단계"}}}) == {}
    assert situate.sentences({"id": "S3", "facts": {"deployed": {"crew": {"total": 0}}}}) == {}


def test_josa():
    assert situate.josa("최소 1동", "으로", "로") == "최소 1동으로"
    assert situate.josa("3.4 m/s", "으로", "로") == "3.4 m/s로"
    assert situate.josa("장비 26대(기타 1)", "이", "가") == "장비 26대(기타 1)가"
    assert situate.josa("산림청장", "이", "가") == "산림청장이"


def test_action_check_and_restated_sentence():
    sit = situate.sentences(S1_R1)["현재 기준 단계"]
    assert check.check_action("확산대응 1단계 발령을 산림청장과 협의하십시오.", sit) == []
    assert any(
        "상황 문장에 없는 숫자" in e for e in check.check_action("20동이 넘으면 협의하십시오.", sit)
    )
    restated = "피해면적 48.5 ha, 평균풍속 3.4 m/s로 1단계입니다. 확산대응 1단계 발령을 산림청장과 협의하십시오."
    assert check.drop_restated(restated, sit) == "확산대응 1단계 발령을 산림청장과 협의하십시오."
    assert (
        check.drop_restated("피해면적 48.5 ha입니다.", sit) == "피해면적 48.5 ha입니다."
    )  # 할 일이 없으면 그대로(검사가 다시 쓰게)


def test_examples_show_action_part_only():
    ex = prompt.examples_of("S1", action_only={"S1-a1"})
    assert ex[0] == "확산대응 N단계 발령을 산림청장과 협의하십시오."  # 상황 부분(값)은 빠짐
    assert "피해면적이 HH:MM에 N ha" in ex[1]  # 상황 문장이 없는 주제는 그대로


ITEM = S1_R1 | {
    "name": "대응단계 판단·격상 검토·지휘권 인계",
    "finding": "현재 기준 1단계",
    "rule_basis": [],
    "sections": [
        {
            "title": "1. 대응단계 판단·격상 검토",
            "blocks": [{"type": "action", "id": "S1-a1", "topic": "현재 기준 단계"}],
        }
    ],
}
CHUNK = {
    "chunk_id": "SM-p091-s01",
    "doc_id": "SM",
    "doc_name": "표준매뉴얼",
    "page": "91",
    "section": "p.91",
    "text": "확산대응 단계 발령은 산림청장과 협의한다",
}


class _Ev:
    def fetch(self, ids):
        return [], list(ids)

    def search(self, query, k=2, exclude=()):
        return [CHUNK | {"score": 0.6}]


class _LLM:
    def __init__(self, text):
        self.text, self.seen = text, []

    def chat(self, messages, schema=None, **kw):
        self.seen.append(messages)
        return json.dumps(
            {
                "proposals": [
                    {
                        "topic": "현재 기준 단계",
                        "category": "협의",
                        "text": self.text,
                        "evidence": ["SM-p091-s01"],
                        "quote": "확산대응 단계 발령은 산림청장과 협의",
                    }
                ]
            },
            ensure_ascii=False,
        )


def test_agent_assembles_situation_plus_action():
    llm = _LLM("피해면적 48.5 ha로 1단계입니다. 확산대응 1단계 발령을 산림청장과 협의하십시오.")
    items, logs = Agent(llm, _Ev(), set(), workers=1).run({"reader": "의성군수"}, [ITEM])
    user = llm.seen[0][1]["content"]
    assert (
        "상황 문장(제안 앞에 그대로 붙음" in user
        and "세 기준 모두 판정 기준상 확산대응 1단계" in user
    )
    para = items[0]["sections"][0]["blocks"][0]
    assert para["body"] == (
        "피해면적 48.5 ha, 평균풍속 3.4 m/s, 시설피해 우려 최소 1동으로 세 기준 모두 판정 기준상 확산대응 1단계에 해당합니다. "
        "확산대응 1단계 발령을 산림청장과 협의하십시오."
    )  # LLM이 되풀이한 상황 문장은 지우고 코드 문장을 앞에
    assert para["text"].endswith("협의하십시오 [p.91].") and para["source"] == "rule+llm"
    assert (
        para["action_text"] == "확산대응 1단계 발령을 산림청장과 협의하십시오."
        and items[0]["state"] == "ok"
    )
    assert logs[0]["situations"]["현재 기준 단계"].startswith("피해면적 48.5 ha")


def test_duplicate_check_uses_whole_paragraph():
    sits = situate.sentences(S1_R1)
    props = [
        {
            "topic": "현재 기준 단계",
            "category": "협의",
            "text": "산림청장과 협의하십시오.",
            "evidence": ["X"],
            "situation": sits["현재 기준 단계"],
        },
        {
            "topic": "5시간 뒤 단계",
            "category": "대기",
            "text": "산림청장과 협의하십시오.",
            "evidence": ["X"],
            "situation": sits["5시간 뒤 단계"],
        },
    ]
    allowed = check.allowed_from(
        json.dumps(S1_R1["facts"], ensure_ascii=False), "확산대응 단계 발령은 산림청장과 협의"
    )
    bad = check.check(
        "S1", props, {"X"}, allowed, "1단계 2단계", set(), "의성군수", situations=sits
    )
    assert not any("같은 내용" in e or "대상만 바꿔" in e for errs in bad.values() for e in errs)


def test_repeated_values_and_org_names():
    sit = situate.sentences(S2_R1)["주택 보호 배치"] + " 괴산리 최소 3동"
    errs = check.check_action(
        "신월리 최소 1동, 괴산리 최소 3동의 주택 주변에 소방차를 배치하십시오.", sit
    )
    assert any("되풀이" in e for e in errs)
    assert check.check_action("그 주택 주변에 소방차와 진화차를 집중 배치하십시오.", sit) == []
    allowed = check.allowed_from("의성군 산림녹지과 · 의성소방서", "")
    p = {
        "category": "즉시",
        "text": "의성군 산림녹색과에게 진화자원 동원을 지시하십시오.",
        "evidence": ["X"],
    }
    assert any(
        "기관 이름 「의성군 산림녹색과」" in e
        for e in check.check_one("S3", p, {"X"}, allowed, "", set(), "의성군수")
    )
    p["text"] = "의성군 산림녹지과장과 의성소방서장에게 안전장구 착용을 확인하도록 지시하십시오."
    assert not any(
        "기관 이름" in e for e in check.check_one("S5", p, {"X"}, allowed, "", set(), "의성군수")
    )


def test_stage_label_is_not_a_repeated_value():
    sit = situate.sentences(S1_R1)["5시간 뒤 단계"]
    assert (
        check.check_action(
            "100 ha에 가까워지면 확산대응 2단계 격상을 산림청장과 협의하십시오.", sit
        )
        == []
    )
    errs = check.check_action("119.7 ha, 17:50에 301.5 ha가 되면 2단계 격상을 협의하십시오.", sit)
    assert errs and all(e.startswith(check.SOFT) for e in errs) and check.hard(errs) == []
    assert check.hard(check.check_action("18:00에 협의하십시오.", sit))  # 지어낸 시각은 뺀다


def test_adnominal_negation_is_allowed():
    allowed = check.allowed_from("괴산리 신월리", "")
    p = {
        "category": "즉시",
        "text": "대피하지 않은 주민은 강제 대피 조치를 취하십시오.",
        "evidence": ["X"],
    }
    assert not any(
        "부정" in e for e in check.check_one("E2", p, {"X"}, allowed, "", set(), "의성군수")
    )
    p["text"] = "주민 대피를 지시하지 마십시오."
    assert any("부정" in e for e in check.check_one("E2", p, {"X"}, allowed, "", set(), "의성군수"))


def test_soft_error_keeps_proposal():
    text = (
        "피해면적 48.5 ha, 평균풍속 3.4 m/s 기준으로 확산대응 1단계 발령을 산림청장과 협의하십시오."
    )
    items, logs = Agent(_LLM(text), _Ev(), set(), workers=1).run({"reader": "의성군수"}, [ITEM])
    assert items[0]["state"] == "ok" and logs[0]["dropped"] == []  # 되풀이만 남으면 빼지 않는다
    assert len(logs[0]["attempts"]) == 3  # 다시 쓰게는 한다


def test_tidy_turns_declarative_action_into_imperative():
    assert (
        check.tidy("마을별로 노약자를 우선 대피시킵니다.")
        == "마을별로 노약자를 우선 대피시키십시오."
    )
    assert check.tidy("대피 완료를 확인합니다") == "대피 완료를 확인하십시오."
    assert (
        check.tidy("확산대응 1단계에 해당합니다.") == "확산대응 1단계에 해당합니다."
    )  # 할 일 동사가 아님
