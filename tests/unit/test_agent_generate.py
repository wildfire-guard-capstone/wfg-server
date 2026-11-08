# ruff: noqa: E501
"""대응 제안 생성 흐름(가짜 LLM · 근거 서버): 판정만 넘김 → 검색 → 제안 → 검사 → 다시 쓰기 → 조립."""

import json

from agent import cite, judgment, prompt
from agent.generate import Agent

ITEM = {
    "id": "E2",
    "name": "대피 대상·순서·시점",
    "status": "order_needed",
    "finding": "위험구역 1곳 128명: 신월리 · 일몰 18:38",
    "facts": {"immediate": [{"name": "신월리", "people": 128, "night": False}]},
    "withheld": [{"field": "장애인 · 어린이 인원", "reason": "리 단위 자료 없음"}],
    "do_not_state": ["장애인 · 어린이 인원"],
    "targets": ["village:1"],
    "rule_basis": ["SM-p075"],
    "anchor_basis": ["SM-p075", "SM-p079"],
    "sections": [
        {
            "title": "1. 대피",
            "blocks": [
                {
                    "type": "action",
                    "id": "E2-a1",
                    "topic": "위험구역 마을 대피",
                    "anchors": ["p.75"],
                },
                {"type": "list", "writer": "조립기", "rows": [{"text": "지금: **신월리**(128명)"}]},
                {"type": "fixed", "writer": "조립기", "text": "아래 마을을 대피시키십시오."},
            ],
        }
    ],
}
COMMON = {"reader": "의성군수", "current_stage": "확산대응 1단계", "commander": {"now": "의성군수"}}
CHUNKS = {
    "SM-p075-s01": {
        "chunk_id": "SM-p075-s01",
        "doc_id": "SM",
        "doc_name": "표준매뉴얼",
        "page": "75",
        "section": "p.75",
        "text": "위험구역 주민은 바로 대피시킨다",
    },
    "SM-p079-s01": {
        "chunk_id": "SM-p079-s01",
        "doc_id": "SM",
        "doc_name": "표준매뉴얼",
        "page": "79",
        "section": "p.79",
        "text": "노약자와 환자를 먼저 대피시킨다",
    },
    "DEC-FDPA-a21-h01-02": {
        "chunk_id": "DEC-FDPA-a21-h01-02",
        "doc_id": "DEC-FDPA",
        "doc_name": "산림재난방지법 시행령",
        "section": "제21조",
        "text": "통합지휘",
    },
}


class FakeEvidence:
    def fetch(self, ids):
        return [CHUNKS[i] for i in ids if i in CHUNKS], [i for i in ids if i not in CHUNKS]

    def search(self, query, k=2, exclude=()):
        return [CHUNKS["SM-p079-s01"] | {"score": 0.7}]


class FakeLLM:
    def __init__(self, answers):
        self.answers, self.calls, self.seen = list(answers), [], []

    def chat(self, messages, schema=None, **kw):
        self.seen.append((messages, schema))
        self.calls.append({"tag": kw.get("tag")})
        return json.dumps({"proposals": self.answers.pop(0)}, ensure_ascii=False)


GOOD = {
    "topic": "위험구역 마을 대피",
    "category": "즉시",
    "text": "신월리 주민 128명을 즉시 대피시키십시오.",
    "evidence": ["SM-p075-s01"],
    "quote": "위험구역 주민은 바로 대피",
}
BAD = {
    "topic": "위험구역 마을 대피",
    "category": "즉시",
    "text": "석탑리 주민을 대피시키십시오.",
    "evidence": ["SM-p079-s01"],
    "quote": "노약자와 환자를 먼저 대피",
}


def test_judgment_drops_slots_and_fixed_sentence():
    j = judgment.judgment_of(ITEM)
    assert "sections" not in j and "anchor_basis" not in j and "status" not in j
    assert j["data"] == ["- 지금: 신월리(128명)"]  # 목록은 넘기고 고정 문장은 뺀다
    assert j["topics"] == [{"topic": "위험구역 마을 대피", "section": "1. 대피", "use": []}]
    assert [b["type"] for b in judgment.data_blocks(ITEM)] == ["list"]


def test_prompt_has_topics_not_slot_evidence():
    j = judgment.judgment_of(ITEM)
    user = prompt.messages(
        COMMON, j, [CHUNKS["SM-p075-s01"] | {"label": "p.75", "origin": "판정 근거"}]
    )[1]["content"]
    # 다룰 주제는 넘기고(v3 꼴), action ID · 주제별 근거 쪽(anchors)은 넘기지 않는다
    assert "위험구역 마을 대피" in user and "E2-a1" not in user and "anchors" not in user
    assert "■ SM-p075-s01" in user and "[문단 꼴 예시]" in user
    props = prompt.schema(["SM-p075-s01"], ["위험구역 마을 대피"])["properties"]["proposals"]
    assert props["items"]["properties"]["evidence"]["items"]["enum"] == ["SM-p075-s01"]
    assert props["items"]["properties"]["topic"]["enum"] == ["위험구역 마을 대피", "그 밖의 제안"]
    assert props["minItems"] == 1 and props["maxItems"] == 2


def test_cite_labels():
    assert cite.label(CHUNKS["SM-p079-s01"]) == "p.79"
    assert cite.label(CHUNKS["DEC-FDPA-a21-h01-02"]) == "시행령 제21조①·②"
    assert cite.label({"chunk_id": "NOTI-MOBL-t0-p1", "doc_id": "NOTI-MOBL"}) == "고시 별표"
    assert cite.label({"chunk_id": "REG-ROLE-t3-p2", "doc_id": "REG-ROLE"}) == "규정 별표 3"
    assert (
        cite.label({"chunk_id": "LAW-DSMA-a38_2", "doc_id": "LAW-DSMA"}) == "재난안전법 제38조의2"
    )
    assert (
        cite.render([CHUNKS["SM-p079-s01"], CHUNKS["SM-p075-s01"], CHUNKS["DEC-FDPA-a21-h01-02"]])
        == "[p.75·79, 시행령 제21조①·②]"
    )


def test_run_retry_then_drop():
    llm = FakeLLM(
        [[GOOD, BAD], [GOOD, BAD], [GOOD, BAD]]
    )  # 두 번 다시 써도 BAD는 그대로 → BAD만 뺀다
    agent = Agent(llm, FakeEvidence(), {"신월리", "석탑리"}, workers=1)
    items, logs = agent.run(COMMON, [ITEM])
    it, lg = items[0], logs[0]
    assert len(llm.calls) == 3 and it["state"] == "partial"
    sec = it["sections"][0]  # v3 배치: 소제목 → 그 주제의 제안 → 그 섹션의 목록
    assert sec["title"] == "1. 대피" and [b["type"] for b in sec["blocks"]] == ["paragraph", "list"]
    assert sec["blocks"][0]["text"] == "신월리 주민 128명을 즉시 대피시키십시오 [p.75]."
    assert sec["blocks"][0]["action"] == "E2-a1" and sec["blocks"][0]["quote_ok"]
    assert lg["dropped"][0]["text"].startswith("석탑리") and lg["messages"][0]["role"] == "system"
    assert "석탑리" in llm.seen[1][0][-1]["content"]  # 다시 쓰기 메시지에 문제가 적힌다


def test_reuse_when_input_same():
    llm = FakeLLM([[GOOD]])
    agent = Agent(llm, FakeEvidence(), {"신월리"}, workers=1)
    items, _ = agent.run(COMMON, [ITEM])
    prev = {
        "E2": {
            "input_key": items[0]["input_key"],
            "proposals": items[0]["proposals"],
            "state": "ok",
            "item": {k: v for k, v in items[0].items() if k not in ("input_key", "proposals")},
            "version": "v1.1",
        }
    }
    again, logs = agent.run(COMMON, [ITEM], prev)
    assert (
        again[0]["state"] == "reused" and logs[0]["reused_from"] == "v1.1" and len(llm.calls) == 1
    )


def test_fix_evidence_moves_to_quoted_chunk():
    from agent.generate import fix_evidence

    by_id = {k: v | {"label": "p"} for k, v in CHUNKS.items()}
    p = {"text": "x", "evidence": ["SM-p075-s01"], "quote": "노약자와 환자를 먼저 대피시킨다"}
    assert fix_evidence(p, by_id)["to"] == ["SM-p079-s01"] and p["evidence"] == ["SM-p079-s01"]
    p2 = {"text": "x", "evidence": ["SM-p075-s01"], "quote": "어디에도 없는 문장입니다"}
    assert fix_evidence(p2, by_id) is None and p2["evidence"] == ["SM-p075-s01"]
