# ruff: noqa: E501
"""에이전트 작성기(가짜 LLM · 근거 서버): 엔진 결과 → agent.generate → 가짜 작성기와 같은 모양."""

import json

import pytest
from rules_example import full_inputs

from agent.clients import LLM, Evidence
from app.core.config import get_settings
from app.propose import get_writer
from app.propose.agent import AgentWriter, WriterUnavailable
from app.propose.fake import FakeWriter
from app.services import prediction
from rules.llm_input import build_llm_input

INCIDENT = {"name": "의성군 안평면 산불 (2025-03-22)", "sigungu": "의성군", "emd": "안평면"}
CHUNK = {
    "chunk_id": "SM-p075-s01",
    "doc_id": "SM",
    "doc_name": "표준매뉴얼",
    "page": "75",
    "section": "p.75",
    "text": "위험구역 주민은 바로 대피시킨다",
}


class FakeEvidence:
    def fetch(self, ids):
        return [], list(ids)

    def search(self, query, k=2, exclude=()):
        return [CHUNK | {"score": 0.7}]


class EchoLLM:
    """받은 형식(schema)에서 첫 주제 · 첫 근거를 골라 제안 하나를 돌려준다."""

    def __init__(self):
        self.calls = []

    def chat(self, messages, schema=None, **kw):
        self.calls.append(kw.get("tag"))
        props = schema["properties"]["proposals"]["items"]["properties"]
        proposal = {
            "topic": props["topic"]["enum"][0],
            "category": "즉시",
            "text": "주민을 대피시키십시오.",
            "evidence": [props["evidence"]["items"]["enum"][0]],
            "quote": "위험구역 주민은 바로 대피",
        }
        return json.dumps({"proposals": [proposal]}, ensure_ascii=False)


class DownLLM:
    def chat(self, messages, schema=None, **kw):
        raise ConnectionRefusedError("연결 거부")


@pytest.fixture(scope="module")
def engine():
    return build_llm_input(full_inputs(), INCIDENT)


def test_writes_every_engine_item_in_the_fake_writer_shape(engine):
    llm = EchoLLM()
    written = AgentWriter(llm, FakeEvidence(), names=lambda: {"신월리"}).write(engine)
    fake = FakeWriter().write(engine)
    assert [w["id"] for w in written] == [it["id"] for it in engine["items"]]
    assert all(set(w) == set(f) for w, f in zip(written, fake, strict=True))  # proposals는 뺀다
    assert all(w["input_key"] for w in written)  # 진짜 작성기는 입력 지문을 남긴다
    assert len(llm.calls) >= len(written)


def test_unreachable_llm_fails_instead_of_saving_an_empty_proposal(engine):
    with pytest.raises(WriterUnavailable):
        AgentWriter(DownLLM(), FakeEvidence()).write(engine)


def test_get_writer_agent_needs_llm_and_evidence():
    with pytest.raises(ValueError, match="llm 설정이 필요"):
        get_writer("agent")
    writer = get_writer("agent", llm=EchoLLM(), evidence=FakeEvidence())
    assert isinstance(writer, AgentWriter) and writer.name == "agent"


def test_writer_from_settings_builds_agent_from_tunnel_urls(monkeypatch):
    monkeypatch.setenv("PROPOSAL_WRITER", "agent")
    monkeypatch.setenv("LLM_URL", "http://gpu:8031")
    monkeypatch.setenv("EVIDENCE_URL", "http://gpu:8032")
    get_settings.cache_clear()
    try:
        writer = prediction.writer_from_settings()
        assert isinstance(writer.llm, LLM) and writer.llm.url == "http://gpu:8031"
        assert isinstance(writer.evidence, Evidence) and writer.evidence.url == "http://gpu:8032"
        assert writer.llm.timeout == 600
        monkeypatch.delenv("EVIDENCE_URL")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="EVIDENCE_URL"):
            prediction.writer_from_settings()
    finally:
        get_settings.cache_clear()
