# ruff: noqa: E501
"""성능 평가 비교 조건(11/18): C1 근거 없음 · C2 단순 RAG · C3 우리 방식(가짜 LLM · 근거 서버)."""

import json

import pytest

from agent import generate, judgment, prompt, retrieve
from agent.generate import Agent

ITEM = {
    "id": "E2",
    "name": "대피 대상·순서·시점",
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
}
FACTS = {
    "situation": "신월리에는 이미 불이 닿았습니다.",
    "status": "신월리 주민은 128명입니다. 대피소는 3곳 모자랍니다.",  # 「3」은 판정에 없는 값
    "judgment": "",
}


class RecEvidence:
    """부른 기록을 남기는 가짜 근거 서버."""

    def __init__(self):
        self.fetched, self.searched = [], []

    def fetch(self, ids):
        self.fetched.append(list(ids))
        return [CHUNKS[i] for i in ids if i in CHUNKS], [i for i in ids if i not in CHUNKS]

    def search(self, query, k=2, exclude=()):
        self.searched.append((query, k))
        return [CHUNKS["SM-p079-s01"] | {"score": 0.7}]


class FakeLLM:
    def __init__(self, answers):
        self.answers, self.calls, self.seen = list(answers), [], []

    def chat(self, messages, schema=None, **kw):
        self.seen.append((messages, schema))
        self.calls.append({"tag": kw.get("tag")})
        a = self.answers.pop(0)
        return a if isinstance(a, str) else json.dumps(FACTS | {"proposals": a}, ensure_ascii=False)


C1_PROP = {
    "topic": "위험구역 마을 대피",
    "category": "즉시",
    "text": "석탑리 주민을 대피시키십시오.",
}
C2_PROP = {
    "topic": "위험구역 마을 대피",
    "category": "즉시",
    "text": "신월리 주민을 대피시키십시오.",
    "evidence": ["SM-p079-s01"],
    "quote": "위험구역 주민은 바로 대피",  # SM-p075의 문장인데 p.79를 달았다(C3라면 근거를 바로잡음)
}


def test_c1_system_and_schema_have_no_evidence():
    s1 = prompt.system("C1")
    assert "발췌" not in s1 and "evidence" not in s1 and "quote" not in s1
    assert "산불 대응에 관한 지식으로 정합니다" in s1
    assert prompt.system("C2") == prompt.SYSTEM == prompt.system("C3")
    sch = prompt.schema([], ["위험구역 마을 대피"], None, with_evidence=False)
    item = sch["properties"]["proposals"]["items"]
    assert set(item["properties"]) == {"topic", "category", "text"}
    assert item["required"] == ["topic", "category", "text"]
    full = prompt.schema(["SM-p075-s01"], ["위험구역 마을 대피"])  # C3 · C2는 예전과 같은 칸 순서
    assert list(full["properties"]["proposals"]["items"]["properties"]) == [
        "topic",
        "category",
        "text",
        "quote",
        "evidence",
    ]
    assert full["properties"]["proposals"]["items"]["required"] == [
        "topic",
        "category",
        "text",
        "evidence",
        "quote",
    ]


def test_c1_prompt_has_no_evidence_section():
    j = judgment.judgment_of(ITEM)
    c1 = prompt.messages(COMMON, j, [], condition="C1")[1]["content"]
    assert "[근거 발췌]" not in c1 and "근거 발췌" not in c1
    assert c1.rstrip().endswith("대응 제안을 JSON으로 쓰십시오.")
    c2 = prompt.messages(
        COMMON, j, [CHUNKS["SM-p079-s01"] | {"label": "p.79", "origin": "검색"}], condition="C2"
    )[1]["content"]
    assert "[근거 발췌]" in c2 and "■ SM-p079-s01" in c2
    assert (
        prompt.no_evidence(
            "이번 상황에 덧붙일 제안(근거 발췌에 그 조치가 있을 때만, 주제마다 하나):"
        )
        == "이번 상황에 덧붙일 제안(주제마다 하나):"
    )


def test_c1_run_once_without_checks_or_evidence():
    ev = RecEvidence()
    llm = FakeLLM([[C1_PROP]])
    items, logs = Agent(llm, ev, {"신월리", "석탑리"}, workers=1, condition="C1").run(
        COMMON, [ITEM]
    )
    it, lg = items[0], logs[0]
    assert len(llm.calls) == 1 and ev.fetched == [] and ev.searched == []  # 검색 없음 · 한 번만
    schema = llm.seen[0][1]
    assert "evidence" not in schema["properties"]["proposals"]["items"]["properties"]
    blocks = it["sections"][0]["blocks"]
    para = next(b for b in blocks if b["type"] == "paragraph")
    # 검사가 없으므로 판정에 없는 마을(석탑리)도 그대로, 근거 표기 없음
    assert (
        para["text"] == "석탑리 주민을 대피시키십시오."
        and para["evidence"] == []
        and para["cite"] == ""
    )
    status = next(b for b in blocks if b.get("step") == "status")
    assert "3곳 모자랍니다" in status["text"]  # 사실부 값 오류도 빼지 않는다(C3라면 뺌)
    assert it["evidence"] == [] and it["state"] == "ok" and lg["condition"] == "C1"


def test_c2_naive_search_once_no_basis_no_fix():
    ev = RecEvidence()
    llm = FakeLLM([[C2_PROP]])
    items, logs = Agent(llm, ev, {"신월리"}, workers=1, condition="C2", k_naive=5).run(
        COMMON, [ITEM]
    )
    it, lg = items[0], logs[0]
    assert ev.fetched == []  # 규칙 엔진 판정 근거(rule_basis)를 가져오지 않는다
    assert ev.searched == [("대피 대상·순서·시점 위험구역 마을 대피", 5)]  # 질의 하나 · 상위 5개
    assert len(llm.calls) == 1
    schema = llm.seen[0][1]
    assert schema["properties"]["proposals"]["items"]["properties"]["evidence"]["items"][
        "enum"
    ] == ["SM-p079-s01"]
    para = next(b for b in it["sections"][0]["blocks"] if b["type"] == "paragraph")
    # 근거를 바로잡지 않는다: quote가 단 청크(p.79)에 없어도 evidence 그대로, quote_ok는 참고 값으로 False
    assert para["evidence"] == ["SM-p079-s01"] and para["quote_ok"] is False
    assert para["text"] == "신월리 주민을 대피시키십시오 [p.79]."
    assert lg["retrieval"]["condition"] == "C2" and "evidence_fixed" not in lg


def test_baseline_retries_only_on_format_error():
    llm = FakeLLM(["JSON 아님", [C1_PROP]])
    items, logs = Agent(llm, RecEvidence(), {"석탑리"}, workers=1, condition="C1").run(
        COMMON, [ITEM]
    )
    assert len(llm.calls) == 2 and "출력 형식 오류" in logs[0]["attempts"][0]["errors"]["-"][0]
    assert logs[0]["attempts"][1]["errors"] == {} and items[0]["state"] == "ok"


def test_baseline_failed_when_no_proposal():
    llm = FakeLLM(["x", "y", "z"])
    items, _ = Agent(llm, RecEvidence(), set(), workers=1, condition="C2").run(COMMON, [ITEM])
    assert len(llm.calls) == 3 and items[0]["state"] == "failed"


def test_input_key_c3_unchanged_and_baselines_differ():
    j = judgment.judgment_of(ITEM)
    keys = {}
    for cond in ("C1", "C2", "C3"):
        llm = FakeLLM([[C2_PROP], [C2_PROP], [C2_PROP]])
        items, _ = Agent(llm, RecEvidence(), {"신월리"}, workers=1, condition=cond).run(
            COMMON, [ITEM]
        )
        keys[cond] = items[0]["input_key"]
    assert keys["C3"] == generate.input_key(j, COMMON, "\n")  # 예전 키 그대로(웹 서비스 재사용)
    assert len(set(keys.values())) == 3


def test_invalid_condition():
    with pytest.raises(ValueError):
        Agent(FakeLLM([]), RecEvidence(), set(), condition="C4")


def test_naive_retrieve_log():
    ev = RecEvidence()
    chunks, log = retrieve.naive(ev, {"name": "대피소 지정"}, ["큰 도로", ""], k=3)
    assert ev.searched == [("대피소 지정 큰 도로", 3)]
    assert chunks[0]["origin"] == "검색" and chunks[0]["label"] == "p.79"
    assert log["queries"] == ["대피소 지정 큰 도로"] and log["basis"] == []
