# ruff: noqa: E501
"""질문 답하기(assistant/tools.py · answer.py · check.py · service.py) — 가짜 LLM · 근거 서버로."""

import json
import urllib.error

import pytest

from assistant import Assistant
from assistant.answer import gather, merge, plan_rules
from assistant.check import check_answer
from assistant.context import DEPLOYED_LABELS, Context
from assistant.service import AssistantUnavailable
from assistant.tools import TOOLS, ToolBox
from tests.unit.assistant_example import P75, make_context


class FakeLLM:
    """tag 순서대로 답을 꺼낸다. 답이 예외면 던진다."""

    def __init__(self, answers):
        self.answers, self.seen, self.calls = dict(answers), [], []

    def chat(self, messages, schema=None, **kw):
        tag = kw.get("tag")
        self.seen.append((tag, messages, schema))
        self.calls.append({"tag": tag})
        a = self.answers[tag]
        if isinstance(a, list):
            a = a.pop(0)
        if isinstance(a, Exception):
            raise a
        return a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)


class FakeEvidence:
    def __init__(self):
        self.queries = []

    def search(self, query, k=4, exclude=()):
        self.queries.append(query)
        return [{**P75, "score": 0.8}]

    def fetch(self, ids):
        return [], list(ids)


def test_deployed_labels_match_engine_adapter():
    from app.repositories.engine_inputs import DEPLOYED_LABELS as ENGINE

    assert DEPLOYED_LABELS == ENGINE


@pytest.mark.parametrize("name", list(TOOLS))
def test_every_tool_runs_with_and_without_proposal(name):
    arg = {"item": "S1", "village": "괴산리", "shelters": "괴산리", "search": "대피"}.get(name, "")
    box = ToolBox(make_context(), FakeEvidence())
    r = box.run(name, arg)
    assert r.facts or r.chunks or r.note, name
    empty = Context(incident={"name": "산불", "status": "진행 중"}, reader="의성군수")
    r0 = ToolBox(empty).run(name, arg)
    assert r0.facts or r0.note, name  # 예측 전이어도 터지지 않고 「없음」을 알린다


def test_resources_reads_live_situation_and_stage_has_basis():
    ctx = make_context()
    ctx.situation["deployed"]["helicopter"] = {"total": 25, "forest": 7, "local": 16, "fire": 2}
    facts = {f.ref: f for f in ToolBox(ctx).run("resources").facts}
    assert facts["situation.deployed.helicopter.total"].value.startswith("25대(산림청 7")
    stage = ToolBox(ctx).run("stage")
    assert any("2단계" in f.value for f in stage.facts)
    assert [c["chunk_id"] for c in stage.chunks] == ["SM-p072-s01"]  # 지휘권 판정 근거(인용 원문)


def test_item_tool_numbers_lists_and_withheld():
    r = ToolBox(make_context()).run("item", "E2")
    refs = [f.ref for f in r.facts]
    assert len([x for x in refs if ".list[" in x]) == 2 and len(refs) == len(set(refs))
    r = ToolBox(make_context()).run("item", "S3")
    assert any("추가 투입 헬기 대수" in f.label and "산정 기준 없음" in f.value for f in r.facts)
    r = ToolBox(make_context()).run("item", "E3")
    assert "발동하지 않아" in r.facts[0].sentence()


def test_village_outside_range_and_shelters():
    box = ToolBox(make_context())
    r = box.run("village", "괴산리")
    assert any("안평초등학교" in f.value for f in r.facts)
    r = box.run("village", "도옥리")
    assert any("8시간 예측 범위 밖" in f.value for f in r.facts)
    # 마을 목록에도 없는 이름: 예측 뒤에는 「위험 마을 목록에 없음」을 근거 있는 값으로(14:20 회차의 박곡리)
    out = box.run("village", "없는리")
    assert "목록에 없음" in out.facts[0].value
    empty = Context(incident={"name": "산불"}, reader="의성군수")
    assert ToolBox(empty).run("village", "없는리").note


def test_rules_plan_and_merge():
    box = ToolBox(make_context(), FakeEvidence())
    p = plan_rules(box, "헬기 몇 대 투입됐어?", None)
    assert ("resources", "") in p.calls and p.route == "STATUS"
    p = plan_rules(box, "박곡리는 왜 일몰 전에 대피해야 해?", None)
    assert p.route == "PROPOSAL" and ("item", "E2") in p.calls and ("village", "박곡리") in p.calls
    assert any(t == "search" for t, _ in p.calls)
    m = merge(None, plan_rules(box, "지금 상황 어때", None), "S1")
    assert m.calls[0] == ("item", "S1")


def test_rules_mode_answer_has_data_refs():
    out = Assistant(make_context()).handle("헬기 몇 대 투입됐어?")
    assert out["type"] == "ANSWER" and "23대" in out["answer"]
    assert out["sentences"][0]["data_refs"][0]["ref"] == "situation.deployed.helicopter.total"


PLAN = {
    "route": "PROPOSAL",
    "calls": [
        {"tool": "item", "arg": "E2"},
        {"tool": "village", "arg": "박곡리"},
        {"tool": "search", "arg": "사전 대피 일몰"},
    ],
}


def _facts_index(llm, needle):
    """answer 프롬프트에서 needle이 든 상황 값의 D번호."""
    user = next(
        m
        for tag, msgs, _ in llm.seen
        if tag.startswith("answer")
        for m in msgs
        if m["role"] == "user"
    )
    for line in user["content"].splitlines():
        if line.startswith("D") and needle in line:
            return line.split(" · ")[0]
    raise AssertionError(needle)


def test_llm_answer_cites_and_renumbers():
    class Smart(FakeLLM):
        def chat(self, messages, schema=None, **kw):
            if kw.get("tag") == "answer0":
                self.seen.append((kw["tag"], messages, schema))
                d = _facts_index(self, "박곡리(280명)")
                return json.dumps(
                    {
                        "sentences": [
                            {
                                "text": "박곡리는 19:50~20:50 일몰 뒤에 불이 닿을 수 있어 일몰 전(18:38)까지 사전 대피를 마치도록 제안했습니다",
                                "data": [d],
                                "docs": [1],
                            },
                            {
                                "text": "잠재적 위험구역은 일몰 전에 사전 대피를 실시합니다",
                                "data": [],
                                "docs": [1],
                            },
                        ],
                        "insufficient": ["박곡리 고령자 수"],
                    },
                    ensure_ascii=False,
                )
            return super().chat(messages, schema, **kw)

    llm = Smart({"plan": PLAN})
    out = Assistant(make_context(), llm, FakeEvidence()).handle(
        "박곡리는 왜 일몰 전에 대피해야 해?"
    )
    assert out["route"] == "PROPOSAL"
    assert out["answer"].startswith("박곡리는 19:50~20:50")
    assert " [1]." in out["answer"] and out["citations"][0]["chunk_id"] == "SM-p075-s02"
    assert "박곡리 고령자 수는 자료와 근거 문서에서 찾지 못해" in out["answer"]
    # 19:50 · 20:50은 단 D번호(E2 제안 문장)에 없고 다른 상황 값에 있어 출처가 더해진다
    refs = out["sentences"][0]["data_refs"]
    assert len(refs) >= 2 and any("19:50" in r["value"] for r in refs[1:])
    schema = next(s for tag, _, s in llm.seen if tag == "answer0")
    assert schema["properties"]["sentences"]["items"]["properties"]["docs"]["items"]["enum"][0] == 1


def test_llm_answer_invented_number_retries_then_drops():
    bad = {
        "sentences": [{"text": "헬기는 40대 투입되었습니다", "data": ["D1"], "docs": []}],
        "insufficient": [],
    }
    llm = FakeLLM(
        {
            "plan": {"route": "STATUS", "calls": [{"tool": "resources", "arg": ""}]},
            "answer0": bad,
            "answer1": bad,
        }
    )
    out = Assistant(make_context(), llm).handle("헬기 몇 대 투입됐어?")
    retry = next(msgs for tag, msgs, _ in llm.seen if tag == "answer1")
    assert "40" in retry[-1]["content"]
    # 두 번 다 떨어지면 「찾지 못했습니다」가 아니라 상황 값 문장으로 대신 답한다(지어낸 40대는 나오지 않음)
    assert out["no_evidence"] is False and out["trace"]["fallback"] == "rules"
    assert "40" not in out["answer"] and "23대" in out["answer"]


def test_empty_llm_answer_falls_back_to_known_values():
    """11-10 평가: 「밤에도 헬기를 띄울 수 있어?」에 EXAONE이 sentences를 비우고 insufficient만 냄."""
    llm = FakeLLM(
        {
            "plan": {
                "route": "STATUS",
                "calls": [{"tool": "weather", "arg": ""}, {"tool": "resources", "arg": ""}],
            },
            "answer0": {"sentences": [], "insufficient": ["헬기 운항 가능 여부"]},
        }
    )
    out = Assistant(make_context(), llm).handle("밤에도 헬기를 띄울 수 있어?")
    assert out["no_evidence"] is False and out["trace"]["fallback"] == "rules"
    first = out["sentences"][0]
    assert "헬기 운항 가능 여부" in first["text"] and "없습니다" in first["text"]
    assert "투입" in out["answer"]  # 아는 값(투입 헬기)도 함께
    assert out["insufficient"] == []  # 이미 문장으로 「없다」고 썼으니 꼬리를 다시 달지 않는다


def test_answer_schema_needs_a_sentence_when_there_is_material():
    from assistant.prompt import answer_schema

    assert answer_schema(3, 0)["properties"]["sentences"]["minItems"] == 1
    assert answer_schema(0, 2)["properties"]["sentences"]["minItems"] == 1
    assert answer_schema(0, 0)["properties"]["sentences"]["minItems"] == 0


def test_tidy_fixes_double_ending_and_inner_codes_and_rejects_missing_label():
    from assistant.check import tidy
    from assistant.tools import Fact

    assert tidy("요양원은 19:20에 위험에 노출됩니다입니다") == "요양원은 19:20에 위험에 노출됩니다"
    assert tidy("5시간 뒤(P5) 면적은 723 ha입니다.") == "5시간 뒤 면적은 723 ha입니다."
    assert tidy("근거는 p.76의 대피명령이다") == "근거는 p.76의 대피명령입니다"
    assert tidy("마을 단위 대피지시를 포함한다.") == "마을 단위 대피지시를 포함합니다."
    assert tidy("대피해야 합니다") == "대피해야 합니다"
    facts = {"D1": Fact("x", "진화예상시간(빠진 정보)", "이 시스템이 판단에 쓰지 않음")}
    for bad in (
        "진화예상시간은 (빠진 정보)로 판단할 수 없습니다",
        "진화예상시간은 MVP에서 제외되었습니다",
    ):
        good, errs = check_answer({"sentences": [{"text": bad, "data": ["D1"]}]}, facts, [])
        assert not good and "내부 말" in errs[0]


def test_unasked_missing_info_sentences_are_dropped():
    """11-10 평가 4차: 재난문자 · 지휘권 · 도로 답 끝마다 「헬기 운항 가능 여부 … 판단할 수 없습니다」."""
    from assistant.check import unasked_unknown

    tail = "헬기 운항 가능 여부, 추가 투입 대수, 최대순간풍속 등은 정보가 없어 판단할 수 없습니다"
    assert unasked_unknown(tail, "재난문자는 어느 지역에 보내야 해?")
    assert unasked_unknown(
        "헬기 운항 가능 여부는 정보가 없어 판단할 수 없습니다", "이 항목은 왜 이렇게 판단했어?"
    )
    assert not unasked_unknown(
        "헬기 운항 가능 여부는 정보가 없어 판단할 수 없습니다", "밤에도 헬기를 띄울 수 있어?"
    )
    assert not unasked_unknown(
        "대피소 실제 개방 여부와 남은 수용 인원은 정보가 없어 판단할 수 없습니다",
        "괴산리 주민은 어디로 대피시키면 돼?",
    )
    assert not unasked_unknown("투입된 헬기는 23대입니다", "재난문자는 어느 지역에 보내야 해?")
    # 6차: 주어 없는 군말은 빼고, 「산정 기준이 없어」는 「몇 대」 질문의 답이다
    assert unasked_unknown("자료에 없는 정보는 판단할 수 없습니다", "헬기를 몇 대 더 불러야 해?")
    assert not unasked_unknown("산정 기준이 없어 판단할 수 없습니다", "헬기를 몇 대 더 불러야 해?")
    from assistant.check import filter_insufficient

    said = [{"text": "진화예상시간은 판단에 사용되지 않으며 정보가 빠져 있습니다"}]
    assert filter_insufficient(["진화예상시간"], "S1 진화예상시간(빠진 정보)", said) == []
    # 5차: 질문 말투(「몇 대 더 불러야」)와 자료 낱말(「추가 투입 대수」)이 달라도 물은 것이다
    assert not unasked_unknown(
        "추가 투입 대수와 배치 위치는 산정 기준이 없어 판단할 수 없습니다",
        "헬기를 몇 대 더 불러야 해?",
    )
    assert unasked_unknown(
        "추가 투입 대수와 배치 위치는 산정 기준이 없어 판단할 수 없습니다",
        "오후에 바람이 더 세지나?",
    )

    llm = FakeLLM(
        {
            "plan": {"route": "STATUS", "calls": [{"tool": "alerts", "arg": ""}]},
            "answer0": {
                "sentences": [
                    {
                        "text": "재난문자는 안평면 괴산리, 신월리, 신안리, 박곡리에 보냅니다",
                        "data": ["D1"],
                    },
                    {"text": tail, "data": ["D1"]},
                ],
                "insufficient": [],
            },
        }
    )
    out = Assistant(make_context(), llm).handle("재난문자는 어느 지역에 보내야 해?")
    assert "헬기" not in out["answer"] and "괴산리" in out["answer"]
    assert out["trace"]["answer_attempts"][0]["unasked"] == [tail]
    assert len(llm.calls) == 2  # 계획 · 답 한 번 — 빼는 것은 다시 묻지 않는다


def test_pre_evacuation_is_not_the_five_hour_zone():
    from assistant.tools import Fact

    # 5차: 「풍속 감소 시간대에 헬기 운용이 가능해지므로」도 운항 판단이다
    facts0 = {"D1": Fact("x", "18:20 바람", "서북서 3.4 m/s")}
    good, errs = check_answer(
        {
            "sentences": [
                {
                    "text": "18:20 이후 풍속이 줄어 헬기 운용이 가능해지므로 일몰 전에 대피합니다",
                    "data": ["D1"],
                }
            ]
        },
        facts0,
        [],
    )
    assert not good and "헬기 운항" in errs[0]

    facts = {"D1": Fact("x", "도옥리 도달", "5~8시간 안 도달(잠재 위험구역)")}
    bad = "사전대피 지시는 도옥리 주민을 대상으로 하며, 이는 5시간 기준 도달 예상 지역에 대한 조치입니다"
    good, errs = check_answer({"sentences": [{"text": bad, "data": ["D1"]}]}, facts, [])
    assert not good and "잠재 위험구역" in errs[0]
    ok = "사전대피는 5~8시간 안에 닿는 도옥리 주민을 대상으로 합니다"
    good, errs = check_answer({"sentences": [{"text": ok, "data": ["D1"]}]}, facts, [])
    assert good and not errs


def test_villages_tool_lists_unhandled_villages_in_arrival_order():
    """11-10 평가 4차: 이미 불이 닿았고 대피 보고 없는 마을을 건너뛰고 5~8시간 마을을 다음 대피로 댐."""
    ctx = make_context()
    w = next(
        f for f in ToolBox(ctx).run("villages", "").facts if f.ref == "context.villages.waiting"
    )
    assert w.value.split(" → ")[0].startswith("안평면 괴산리")
    assert "신안리" in w.value and w.value.index("신안리") < w.value.index("박곡리")
    ctx.situation["villages"]["101"] = {"evacuated": True}  # 괴산리 대피 완료 정정 뒤
    w = next(
        f for f in ToolBox(ctx).run("villages", "").facts if f.ref == "context.villages.waiting"
    )
    assert "괴산리" not in w.value and w.value.startswith("안평면 신월리")


def test_tool_texts_from_11_10_eval():
    box = ToolBox(make_context())
    s1 = box.run("item", "S1")
    assert not any("생성 상태" in f.label for f in s1.facts)  # 정상 생성이면 알리지 않음
    assert all("MVP" not in f.value for f in s1.facts)
    assert any("진화예상시간" in f.label and "판단에 쓰지 않음" in f.value for f in s1.facts)
    ctx = make_context()
    ctx.bundles["roads"]["sections"] = [
        {"ref": "안평의성로", "section": "신월리", "km": 0.0, "from": "17:20", "until": "18:20"}
    ]
    road = ToolBox(ctx).run("roads", "").facts[0]
    assert "0 km" not in road.value and "가장자리" in road.value
    # 대피 준비(Set) 읍면도 재난문자 · 방송 대상임을 값에 밝힌다
    ctx.bundles["alerts"]["areas"].append(
        {
            "sigungu": "의성군",
            "emd": "의성읍",
            "level": "set",
            "villages": ["철파리"],
            "change": "new",
        }
    )
    areas = [f for f in ToolBox(ctx).run("alerts", "").facts if "areas" in f.ref]
    assert all("재난문자 · 방송 대상" in f.label for f in areas)
    assert "준비하라고 알림" in areas[1].value and "철파리" in areas[1].value


def test_changes_tool_follows_app_change_judgement():
    """앱이 넣어 준 「변경」 판단(PROP-04와 같은 규칙)을 따른다 — 표현만 바뀐 것은 변경 아님."""
    ctx = make_context()
    items = [dict(i) for i in ctx.items]  # S1 · S3 · E2
    ctx.previous = {
        "version": "v1.1",
        "items": [{**items[0], "text": "다른 문장"}, items[1]],
        "changes": {"S1": "WORDING_ONLY", "S3": "CHANGED", "E2": "NEW"},
    }
    facts = {f.label: f.value for f in ToolBox(ctx).run("changes", "").facts}
    assert not any(k.startswith("S1") for k in facts)
    assert any(k.startswith("S3") and "내용이 바뀜" in v for k, v in facts.items())
    assert any(k.startswith("E2") and "새로 생김" in v for k, v in facts.items())
    # 앱 판단이 없으면(CLI) 문장 · 구분을 그대로 비교한다
    del ctx.previous["changes"]
    facts = {f.label: f.value for f in ToolBox(ctx).run("changes", "").facts}
    assert any(k.startswith("S1") and "내용이 바뀜" in v for k, v in facts.items())
    assert not any(k.startswith("S3") for k in facts)


def test_check_rejects_unsourced_fake_village_and_inner_refs():
    from assistant.tools import Fact

    facts = {"D1": Fact("x", "괴산리 인구", "147명")}
    good, errs = check_answer(
        {
            "sentences": [
                {"text": "괴산리 인구는 147명입니다 [1]", "data": ["D1"]},
                {"text": "괴산리 인구는 [상황 값] D1에 따르면 147명입니다", "data": ["D1"]},
                {"text": "출처 없는 문장입니다", "data": [], "docs": []},
                {"text": "가짜리 주민 147명을 대피시키십시오", "data": ["D1"]},
                {"text": "괴산리 인구 147명", "data": ["D1"]},
            ]
        },
        facts,
        [],
    )
    assert [g["text"] for g in good] == ["괴산리 인구는 147명입니다"]
    assert any("내부 말" in e for e in errs) and any("출처" in e for e in errs)
    assert any("가짜리" in e for e in errs) and any("끝내십시오" in e for e in errs)


def test_check_forbids_judging_missing_info():
    """11-09 EXAONE 평가: 「밤에도 헬기를 띄울 수 있습니다 … 운항에 무리가 없습니다」 · 「최대 순간 풍속」."""
    from assistant.tools import Fact

    facts = {
        "D1": Fact("w", "22:20 바람", "서남서풍 0.9 m/s"),
        "D2": Fact("u", "헬기 운항 가능 여부(빠진 정보)", "운항 기준 없음"),
    }
    bad = [
        "밤에도 헬기를 띄울 수 있습니다",
        "22:20 바람은 0.9 m/s로 헬기 운항에 무리가 없습니다",
        "최대 순간 풍속은 0.9 m/s입니다",
    ]
    ok = "헬기 운항 가능 여부는 정보가 없어 판단할 수 없습니다"
    good, errs = check_answer(
        {"sentences": [{"text": t, "data": ["D1", "D2"]} for t in [*bad, ok]]}, facts, []
    )
    assert [g["text"] for g in good] == [ok]
    assert len(errs) == 3


def test_check_adds_document_that_holds_the_number():
    """숫자가 상황 값이 아닌 근거 문서에 있으면 그 문서를 출처로 더한다(평균풍속 11 m/s → p.73)."""
    from assistant.tools import Fact

    facts = {"D1": Fact("s", "현재 기준 대응단계", "확산대응 1단계")}
    chunks = [
        {
            "chunk_id": "SM-p073-s02",
            "label": "p.73",
            "text": "평균풍속 11 m/s 이상, 진화예상시간 48시간 이상",
        }
    ]
    good, errs = check_answer(
        {
            "sentences": [
                {"text": "확산대응 2단계 풍속 기준은 11 m/s 이상입니다", "data": ["D1"], "docs": []}
            ]
        },
        facts,
        chunks,
    )
    assert errs == [] and good[0]["docs"] == [1]


def test_insufficient_kept_only_when_asked():
    from assistant.check import filter_insufficient

    said = [{"text": "추가 투입 헬기 대수는 산정 기준이 없어 알 수 없습니다"}]
    assert filter_insufficient(["추가 투입 헬기 대수"], "오후에 바람이 더 세지나?", []) == []
    assert filter_insufficient(["추가 투입 헬기 대수"], "헬기를 몇 대 더 불러야 해?", []) == [
        "추가 투입 헬기 대수"
    ]
    assert filter_insufficient(["추가 투입 헬기 대수"], "헬기를 몇 대 더 불러야 해?", said) == []


def test_same_label_citations_merge():
    from assistant.answer import Sources, compose

    chunks = [
        {"chunk_id": "SM-p076-s01", "label": "p.76", "text": "가"},
        {"chunk_id": "SM-p076-s02", "label": "p.76", "text": "나"},
        {"chunk_id": "LAW-DSMA-a40", "label": "재난안전법 제40조", "text": "다"},
    ]
    out = compose(
        [{"text": "첫 문장입니다", "docs": [1, 2]}, {"text": "둘째 문장입니다", "docs": [3, 2]}],
        Sources([], [], chunks),
        [],
    )
    assert out["answer"] == "첫 문장입니다 [1]. 둘째 문장입니다 [2][1]."
    assert out["citations"][0]["chunk_ids"] == ["SM-p076-s01", "SM-p076-s02"]
    assert [c["label"] for c in out["citations"]] == ["p.76", "재난안전법 제40조"]


def test_out_of_scope_and_greeting():
    llm = FakeLLM(
        {"intent": {"intent": "QUESTION"}, "plan": {"route": "OUT_OF_SCOPE", "calls": []}}
    )
    out = Assistant(make_context(), llm).handle("오늘 점심 메뉴 추천해 줘")
    assert out["route"] == "OUT_OF_SCOPE" and "산불 대응" in out["answer"]
    out = Assistant(make_context()).handle("안녕하세요")
    assert out["route"] == "GUIDE"


def test_item_code_question_always_includes_item():
    llm = FakeLLM(
        {
            "plan": {"route": "STATUS", "calls": [{"tool": "weather", "arg": ""}]},
            "answer0": {"sentences": [], "insufficient": []},
            "answer1": {"sentences": [], "insufficient": []},
        }
    )
    out = Assistant(make_context(), llm).handle("이건 왜 이렇게 나왔어?", item_code="s1")
    assert out["tools"][0] == {"tool": "item", "arg": "S1"}


def test_llm_down_raises_unavailable():
    llm = FakeLLM({"plan": urllib.error.URLError("refused")})
    with pytest.raises(AssistantUnavailable):
        Assistant(make_context(), llm).handle("헬기 몇 대 투입됐어?")


def test_correction_goes_through_service():
    out = Assistant(make_context()).handle("괴산리 대피 완료")
    assert out["type"] == "CORRECTION_PREVIEW" and out["trace"]["intent"] == "CORRECTION"


def test_gather_dedupes_and_puts_judged_chunks_first():
    box = ToolBox(make_context(), FakeEvidence())
    plan = merge(None, plan_rules(box, "박곡리는 왜 일몰 전에 대피해야 해?", None), None)
    src = gather(box, plan)
    ids = [c["chunk_id"] for c in src.chunks]
    assert ids.count("SM-p075-s02") == 1
    assert len({f.ref for _, f in src.facts}) == len(src.facts)
