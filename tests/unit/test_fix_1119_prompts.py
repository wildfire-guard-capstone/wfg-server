# ruff: noqa: E501
"""11-19 성능 평가 개선(결과 덱 7쪽 P1 ~ P3) + 어시스턴트 「묻는 것」(A 방식) + 행정리 이름."""

import json

from agent import check, prompt
from assistant import Assistant
from assistant import prompt as aprompt
from assistant.answer import ask_rules, merge, plan_rules
from assistant.context import ITEM_NAMES
from assistant.tools import JUDGED_BY, TOOLS, ToolBox
from tests.unit.assistant_example import make_context
from tests.unit.test_agent_check import ALLOWED, EV, EV_TEXT, JUDGED, NAMES, _c
from tests.unit.test_assistant_answer import FakeEvidence, FakeLLM

# ── 제안(agent) ──────────────────────────────────────────────────────────


def test_p1_no_fixed_count_in_any_condition():
    for c in prompt.CONDITIONS:
        s = prompt.system(c)
        assert "1~5개" not in s and "개수는 정하지 않고" in s
    assert prompt.MAX_PROPOSALS == 10


def test_p2_split_by_deadline_is_allowed_but_same_deadline_is_not():
    split = [
        {
            "category": "즉시",
            "text": "신월리 주민을 즉시 대피시키십시오.",
            "evidence": ["SM-p079-s02"],
        },
        {
            "category": "대기",
            "text": "석탑리 주민을 14:50까지 대피시키십시오.",
            "evidence": ["SM-p079-s02"],
        },
    ]
    bad = check.check("E2", split, EV, ALLOWED, JUDGED + " 석탑리", NAMES)
    assert not any("모아" in e for errs in bad.values() for e in errs)
    same = [dict(split[0]), dict(split[1], text="석탑리 주민을 즉시 대피시키십시오.")]
    bad = check.check("E2", same, EV, ALLOWED, JUDGED + " 석탑리", NAMES)
    assert 1 in bad and any("모아" in e for e in bad[1])
    assert check.deadline_of("09:05까지, 즉시") == ("9:05", "즉시")


def test_p2_rule_text_keeps_each_target_deadline():
    s = prompt.system("C3")
    assert "시한이 다른 대상을 하나의 시한으로 합치지 않습니다" in s


def test_p3_missing_data_and_relative_time_rules():
    s = prompt.system("C3")
    assert "그 값으로 판단 · 제안을 만들지 않습니다" in s
    assert "상대 시각으로 바꿔 쓰지 않고" in s and "「N시간 뒤 N단계」" not in s
    # 세 조건 지시문은 근거 한 줄만 다르다(재설계 P6) — 새 줄도 세 조건 같음
    a, b = prompt.system("C1"), prompt.system("C3")
    assert a.replace(prompt.EVIDENCE_RULE["C1"], "") == b.replace(prompt.EVIDENCE_RULE["C3"], "")


def test_rule_part_carries_commander_handover_window():
    # RE-1(main #55) steps가 C3 규칙층 구획까지 와야 S1 밖 항목도 「5시간 뒤 이관」으로 쓰지 않는다
    common = {
        "commander": {
            "now": "의성군수",
            "next": "경상북도지사",
            "steps": [
                {
                    "title": "경상북도지사",
                    "at": "13:50 ~ 14:50",
                    "why": "피해면적 100 ha 이상 대형산불",
                }
            ],
        }
    }
    head = prompt.rule_parts(common, {"finding": "x"})[0]
    assert (
        "지휘권자: 지금 의성군수 → 다음 경상북도지사(13:50 ~ 14:50, 피해면적 100 ha 이상 대형산불)"
        in head
    )
    same = prompt.rule_parts(
        {"commander": {"now": "경상북도지사", "next": "경상북도지사", "steps": []}}, {}
    )[0]
    assert "지휘권자: 지금 경상북도지사\n" in same + "\n" and "→ 다음" not in same


def test_haengjeongri_rule_matches_engine_label():
    # 행정리 이름 검사 자체는 main #59(ADMIN_VILLAGE)가 맡는다 — 여기서는 지시문이 엔진 꼴(D2)과 맞는지만
    s = prompt.system("C3")
    assert "「○○리(○○1리)」) 그대로 옮기고" in s and "인구는 ○○리 값" in s
    errs = _c("신월1리 주택 최소 2동 주변에 소방차와 진화차를 집중 배치하십시오.")
    assert any("신월1리" in e for e in errs)  # 판정 · 근거에 없는 행정리(#59)
    judged = JUDGED + " 신월리(신월1리)"
    ok = check.check_one(
        "S2",
        {
            "category": "즉시",
            "text": "신월리(신월1리) 주택 최소 2동 주변에 소방차와 진화차를 집중 배치하십시오.",
            "evidence": ["SM-p079-s02"],
        },
        EV,
        check.allowed_from(judged, EV_TEXT),
        judged,
        NAMES,
        "의성군수",
    )
    assert not any("신월1리" in e for e in ok)


# ── 어시스턴트 ────────────────────────────────────────────────────────────


def test_item_names_follow_rule_engine():
    from rules.items import e1, e2, e3, e4, e5, e6, s1, s2, s3, s4, s5, s6

    engine = {m.ID: m.NAME for m in (s1, s2, s3, s4, s5, s6, e1, e2, e3, e4, e5, e6)}
    assert ITEM_NAMES == engine
    assert set(JUDGED_BY) == set(engine) and all(t in TOOLS for v in JUDGED_BY.values() for t in v)


def test_plan_schema_asks_first_and_prompt_defines_five():
    sch = aprompt.plan_schema()
    assert list(sch["properties"])[0] == "ask" and sch["required"][0] == "ask"
    assert sch["properties"]["ask"]["enum"] == ["값", "이유", "할 일", "규정", "확인"]
    for a in aprompt.ASKS:
        assert f"  {a} = " in aprompt.PLAN_SYSTEM


def test_rules_ask_fallback():
    assert ask_rules("이건 왜 이렇게 나왔어?", "S1") == "이유"
    assert ask_rules("대피명령 권한은 누구한테 있어?", None) == "규정"
    assert ask_rules("헬기를 더 불러야 할까?", None) == "할 일"
    # 정규식의 한계(「불러야 해」는 못 잡음 — 그래서 LLM의 ask를 먼저 쓴다, 11-19 비교 정규식 0.305)
    assert ask_rules("헬기를 더 불러야 해?", None) == "값"
    assert ask_rules("헬기 몇 대 투입됐어?", None) == "값"


class KwLLM(FakeLLM):
    def chat(self, messages, schema=None, **kw):
        self.kw = getattr(self, "kw", {}) | {kw.get("tag"): kw}
        return super().chat(messages, schema, **kw)


def test_ask_goes_to_answer_form_with_temperature_zero_and_item_search():
    llm = KwLLM(
        {
            "plan": {"ask": "이유", "route": "PROPOSAL", "calls": [{"tool": "weather", "arg": ""}]},
            "answer0": {"sentences": [], "insufficient": []},
            "answer1": {"sentences": [], "insufficient": []},
        }
    )
    ev = FakeEvidence()
    out = Assistant(make_context(), llm, ev).handle("이 항목은 왜 이렇게 판단했어?", item_code="S1")
    assert llm.kw["plan"]["temperature"] == 0.0
    assert out["trace"]["plan"]["ask"] == "이유"
    # 이유를 묻는데 검색이 없으면 넣는다 — 질문 말(지시어) 대신 항목 이름으로
    assert {"tool": "search", "arg": "대응단계 판단·격상 검토·지휘권 인계 판단 기준"} in out[
        "tools"
    ]
    user = next(
        m["content"]
        for tag, msgs, _ in llm.seen
        if tag == "answer0"
        for m in msgs
        if m["role"] == "user"
    )
    assert "[묻는 것] 이유 — 판정 · 제안을 정한 요소와 그 기준" in user
    # 판정 자료(요소별 기준)가 질문 말과 상관없이 들어간다
    assert "현재 기준 피해면적" in user or "context.stage" in json.dumps(
        out["trace"]["sources"]["fact_list"], ensure_ascii=False
    )


def test_item_tool_always_carries_judgment_facts():
    refs = [f.ref for f in ToolBox(make_context()).run("item", "S1").facts]
    assert "items.S1.finding" in refs and any(r.startswith("context.stage.now") for r in refs)
    assert len(refs) == len(set(refs))


def test_commander_steps_reach_stage_tool():
    ctx = make_context()
    ctx.bundles["commander"] = dict(ctx.bundles.get("commander") or {}) | {
        "steps": [
            {
                "p": 2,
                "from": "13:50",
                "until": "14:50",
                "title": "경상북도지사",
                "level": "sido",
                "reasons": [{"kind": "area", "text": "피해면적 100 ha 이상 대형산불"}],
            }
        ]
    }
    facts = {f.ref: f for f in ToolBox(ctx).run("stage").facts}
    assert (
        facts["context.commander.steps[0]"].value
        == "13:50 ~ 14:50 경상북도지사(사유: 피해면적 100 ha 이상 대형산불)"
    )


def test_haengjeongri_question_finds_beopjeongri():
    box = ToolBox(make_context(), FakeEvidence())
    assert [v["name"] for v in box.find_villages("괴산1리")][:1] == ["괴산리"]
    assert [v["name"] for v in box.find_villages("안평면 괴산 2리")] == ["괴산리"]
    p = plan_rules(box, "괴산1리 대피 언제까지 해야 해?", None)
    assert ("village", "괴산리") in p.calls
    assert "○○1리" in aprompt.ANSWER_SYSTEM and "○○1리" in aprompt.PLAN_SYSTEM


def test_village_tools_show_admin_and_edge_villages():
    # 묶음 줄에 대피 대상 행정리 · 경계만 걸친 리(edge)
    ctx = make_context()
    vb = ctx.bundles["villages"]
    first = (vb.get("burning") or vb.get("p5"))[0]
    first["admin_villages"] = [{"id": 9, "name": first["name"].replace("리", "1리"), "zone": "p5"}]
    vb["edge"] = [{"id": 777, "name": "용기리", "emd": "안계면", "boundary_p": 6}]
    ctx.villages = list(ctx.villages) + [{"id": 777, "name": "용기리", "emd": "안계면"}]
    box = ToolBox(ctx)
    labels = [f.label for f in box.run("villages").facts]
    admin = first["name"].replace("리", "1리")
    assert any(f"{first['name']}({admin})" in x for x in labels)
    assert any("경계만" in x for x in labels)
    facts = {f.label: f.value for f in box.run("village", "용기리").facts}
    assert any("대피 대상 아님" in v for v in facts.values())
    facts = {f.label: f.value for f in box.run("village", admin).facts}
    assert any(admin in v and "이 행정리 몫의 추정값" in v for v in facts.values())


def test_road_question_goes_to_e4_not_alerts():
    box = ToolBox(make_context(), FakeEvidence())
    p = plan_rules(box, "통제해야 할 도로가 있어?", None)
    assert ("item", "E4") in p.calls and ("item", "E5") not in p.calls
    m = merge(None, plan_rules(box, "재난문자는 어느 지역에 보내야 하나요?", None), None)
    assert ("item", "E5") in m.calls


def test_relative_time_must_match_base():
    # 기준 12:50: 「5시간 뒤인 14:50」은 틀림(14:50은 100 ha를 넘는 때), 「5시간 뒤(17:50)」는 맞음
    errs = check.relative_time_errors("5시간 뒤인 14:50에는 100 ha를 넘을 수 있습니다.", "12:50")
    assert errs and "17:50" in errs[0]
    assert check.relative_time_errors("5시간 뒤(17:50) 461.9 ha로 커질 수 있습니다.", "12:50") == []
    assert check.relative_time_errors("23:00에서 2시간 뒤인 01:00", "23:00") == []


def test_possibility_question_gets_search_even_as_value():
    box = ToolBox(make_context(), FakeEvidence())
    llm_plan = plan_rules(box, "밤에도 헬기를 띄울 수 있어?", None)
    llm_plan.ask = "값"
    llm_plan.calls = [("weather", "")]
    m = merge(
        llm_plan,
        plan_rules(box, "밤에도 헬기를 띄울 수 있어?", None),
        None,
        text="밤에도 헬기를 띄울 수 있어?",
        can_search=True,
    )
    assert any(t == "search" for t, _ in m.calls) and m.ask == "값"
