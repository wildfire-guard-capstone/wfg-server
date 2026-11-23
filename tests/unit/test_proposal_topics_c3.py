# ruff: noqa: E501
"""11/23 다룰 주제(C3): 규칙 엔진이 켠 주제를 C3 규칙층 구획에 넘기고, 제안마다 topic 칸을 받는다."""

import copy

import pytest

from agent import judgment, prompt
from agent.generate import Agent, topic_errors
from tests.unit.test_agent_generate import CHUNKS, COMMON, GOOD, ITEM, FakeEvidence, FakeLLM

PRE = "잠재 위험구역 사전대피"


def two_topics():
    """E2에 주제 두 개(위험구역 마을 대피 · 잠재 위험구역 사전대피)."""
    item = copy.deepcopy(ITEM)
    item["sections"].append(
        {
            "title": "2. 사전대피",
            "blocks": [{"type": "action", "id": "E2-a2", "topic": PRE, "use": ["pre_evac"]}],
        }
    )
    return item


PRE_GOOD = GOOD | {
    "topic": PRE,
    "category": "대기",
    "text": "신월리 노약자의 사전대피 차량을 준비하도록 읍면에 지시하십시오.",
}


def run(item, answers, cond="C3"):
    llm = FakeLLM(answers)
    agent = Agent(llm, FakeEvidence(), {"신월리", "석탑리"}, workers=1, condition=cond)
    items, logs = agent.run(COMMON, [item])
    return items[0], logs[0], llm


def user_text(llm, i=0):
    return llm.seen[i][0][1]["content"]


def test_c3_prompt_lists_topics_and_schema_requires_them():
    item, lg, llm = run(two_topics(), [[GOOD, PRE_GOOD]])
    user = user_text(llm)
    assert "[다룰 주제 — 규칙층이 이번 판정으로 켠 주제입니다" in user
    assert "- 위험구역 마을 대피 (소제목 1. 대피 · 쓸 판정 값 —)" in user
    assert f"- {PRE} (소제목 2. 사전대피 · 쓸 판정 값 pre_evac)" in user
    # 주제 구획은 규칙층 판정 구획 뒤, 근거 발췌 앞(C3 구획 안)
    assert user.index("[규칙층 판정") < user.index("[다룰 주제") < user.index("[근거 발췌]")
    schema = llm.seen[0][1]
    props = schema["properties"]["proposals"]
    assert list(props["items"]["properties"])[0] == "topic"  # 주제를 먼저 고르고 쓴다
    assert props["items"]["properties"]["topic"]["enum"] == ["위험구역 마을 대피", PRE]
    assert props["items"]["required"][0] == "topic"
    assert props["minItems"] == props["maxItems"] == 2  # 주제마다 하나
    assert lg["topics"] == ["위험구역 마을 대피", PRE] and item["state"] == "ok"
    assert len(llm.calls) == 1


@pytest.mark.parametrize("cond", ("C1", "C2"))
def test_c1_c2_unchanged(cond):
    """평가 기준선(C1 · C2)은 그대로: 주제 구획 · topic 칸 없음, 제안 수 1 ~ MAX."""
    j = judgment.judgment_of(two_topics())
    msgs = prompt.messages(
        COMMON,
        j,
        [CHUNKS["SM-p075-s01"] | {"label": "p.75", "origin": "검색"}],
        condition=cond,
        raw_table="[원자료 표]\n- 신월리 128명",
        topics=j["topics"],  # 넘겨도 C3가 아니면 쓰지 않는다
    )
    assert "[다룰 주제" not in msgs[1]["content"]
    schema = prompt.schema(["SM-p075-s01"], cond != "C1")
    items = schema["properties"]["proposals"]
    assert "topic" not in items["items"]["properties"]
    assert (items["minItems"], items["maxItems"]) == (1, prompt.MAX_PROPOSALS)


def test_system_text_still_same_for_three_conditions():
    """주제는 C3 구획에만 넣으므로 지시문(SYSTEM)은 근거 규칙 한 줄만 다르다(11/19 원칙 그대로)."""
    c1, c2, c3 = (prompt.system(c).splitlines() for c in prompt.CONDITIONS)
    assert c2 == c3
    assert sum(a != b for a, b in zip(c1, c3, strict=True)) == 1
    assert "[다룰 주제" not in prompt.system("C3")


def test_paragraphs_follow_topic_order_and_keep_topic():
    item, _, _ = run(two_topics(), [[PRE_GOOD, GOOD]])  # LLM이 순서를 바꿔 써도
    paras = [b for b in item["sections"][0]["blocks"] if b["type"] == "paragraph"]
    assert [p["topic"] for p in paras] == ["위험구역 마을 대피", PRE]  # 엔진 주제 순서로


def test_missing_topic_rewrites_then_partial():
    """한 주제가 끝까지 빠지면 다시 쓰게 하고(최대 3번), 남은 제안은 살리되 상태는 partial."""
    item, lg, llm = run(two_topics(), [[GOOD], [GOOD], [GOOD]])
    assert len(llm.calls) == 3
    retry = llm.seen[1][0][-1]["content"]
    assert f"다룰 주제 「{PRE}」의 제안이 없습니다" in retry
    assert lg["topics_missing"] == [PRE] and item["state"] == "partial"
    paras = [b for b in item["sections"][0]["blocks"] if b["type"] == "paragraph"]
    assert [p["topic"] for p in paras] == ["위험구역 마을 대피"]


def test_missing_topic_fixed_on_retry():
    item, lg, llm = run(two_topics(), [[GOOD], [GOOD, PRE_GOOD]])
    assert len(llm.calls) == 2 and item["state"] == "ok" and "topics_missing" not in lg


def test_topic_errors():
    names = ["가", "나"]
    assert topic_errors([{"topic": "가"}, {"topic": "나"}], names) == []
    errs = topic_errors([{"topic": "가"}, {"topic": "가"}], names)
    assert any("「나」의 제안이 없습니다" in e for e in errs)
    assert any("「가」의 제안이 2개입니다" in e for e in errs)


def test_item_without_topics_keeps_free_proposals():
    """주제가 없는 항목은 예전처럼 주제 없이 1 ~ MAX개."""
    item = copy.deepcopy(ITEM)
    item["sections"][0]["blocks"] = [
        b for b in item["sections"][0]["blocks"] if b["type"] != "action"
    ]
    out, lg, llm = run(item, [[GOOD]])
    assert "[다룰 주제" not in user_text(llm) and lg["topics"] == []
    props = llm.seen[0][1]["properties"]["proposals"]
    assert "topic" not in props["items"]["properties"] and props["minItems"] == 1
    para = next(b for b in out["sections"][0]["blocks"] if b["type"] == "paragraph")
    assert "topic" not in para
