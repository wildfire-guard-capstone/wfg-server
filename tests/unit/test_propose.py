"""제안 작성기: 가짜 작성기가 엔진 결과로 진짜 에이전트와 같은 모양의 항목 결과를 만든다."""

import json

import pytest
from rules_example import full_inputs

from agent.judgment import data_blocks, topics_of
from agent.prompt import CATEGORIES
from app.propose import get_writer
from app.propose.fake import TEST_CHUNK, FakeWriter
from rules.llm_input import build_llm_input

INCIDENT = {"name": "의성군 안평면 산불 (2025-03-22)", "sigungu": "의성군", "emd": "안평면"}
EVIDENCE_KEYS = ("chunk_id", "label", "doc_name", "section", "page", "origin", "text")


@pytest.fixture(scope="module")
def engine():
    return build_llm_input(full_inputs(), INCIDENT)


@pytest.fixture(scope="module")
def written(engine):
    return {it["id"]: it for it in FakeWriter().write(engine)}


def blocks(item, *types):
    """문서 순서대로(제안 문단 뒤에 그 문단의 자세한 자료 details, 11/18)."""
    flat = [d for s in item["sections"] for b in s["blocks"] for d in [b, *b.get("details", [])]]
    return [b for b in flat if b["type"] in types]


def test_one_result_per_engine_item_in_order(engine, written):
    assert list(written) == [it["id"] for it in engine["items"]]
    for it in written.values():
        assert set(it) == {
            "id",
            "name",
            "state",
            "finding",
            "withheld",
            "sections",
            "evidence",
            "input_key",
        }
        assert it["state"] == "ok"
        assert it["input_key"] is None  # 가짜 제안은 다음 회차에 다시 쓰지 않는다
    json.dumps(list(written.values()), ensure_ascii=False)  # jsonb로 저장할 수 있다


def test_four_steps_then_engine_lists(engine, written):
    """11/17 배치: 상황 · 상태 · 판단(fact) → 제안(paragraph).
    규칙 엔진 목록은 제안에 붙지 않고 그 아래 섹션에 남는다(11/19).
    11/23: 제안 문단에는 다룰 주제(topic)가 엔진 주제 순서대로 붙는다."""
    e2 = written["E2"]
    assert [b["type"] for b in blocks(e2, "fact", "paragraph", "list")] == [
        "fact",
        "fact",
        "fact",
        "paragraph",
        "paragraph",
        "list",
        "list",
    ]
    assert e2["sections"][0]["title"] == "" and len(e2["sections"]) >= 2
    assert all("details" not in p for p in blocks(e2, "paragraph"))
    e2_engine = next(it for it in engine["items"] if it["id"] == "E2")
    engine_topics = [t["topic"] for t in topics_of(e2_engine) if t["topic"]]
    assert [p["topic"] for p in blocks(e2, "paragraph")] == engine_topics
    assert [b["step"] for b in blocks(e2, "fact")] == ["situation", "status", "judgment"]
    first, _second = blocks(e2, "paragraph")
    assert first["text"] == "[테스트] 위험구역 마을 대피 제안 문장입니다 [테스트 문서]."
    assert first["body"] == "[테스트] 위험구역 마을 대피 제안 문장입니다."
    assert first["cite"] == "[테스트 문서]"
    assert first["evidence"] == ["TEST-0001"]
    assert e2["evidence"] == [{k: TEST_CHUNK[k] for k in EVIDENCE_KEYS}]
    assert e2["evidence"][0]["text"].startswith("테스트 원문")


def test_engine_tables_lists_notes_kept_as_is(engine, written):
    for item in engine["items"]:
        assert blocks(written[item["id"]], "list", "table", "note") == data_blocks(item)


def test_item_without_topics_gets_one_proposal_first(written):
    e6 = written["E6"]  # 다룰 주제 없음(엔진 action 블록 없음)
    assert e6["sections"][0]["title"] == ""  # 네 단계 섹션은 소제목 없음
    (para,) = blocks(e6, "paragraph")
    assert "topic" not in para
    assert blocks(e6, "table")  # 요청 표는 그대로


def test_categories_rotate_so_all_four_show(written):
    paras = [p for it in written.values() for p in blocks(it, "paragraph")]
    assert {p["category"] for p in paras} == set(CATEGORIES)


def test_get_writer():
    assert isinstance(get_writer("fake"), FakeWriter)
    with pytest.raises(ValueError, match="모르는 작성기"):
        get_writer("exaone")
