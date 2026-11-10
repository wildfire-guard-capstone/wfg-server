"""가짜 작성기: LLM 없이 제안서를 채운다(흐름 · 저장 · 화면 시험용).

문장 · 구분 · 근거만 가짜이고, 소제목 · 표 · 목록 · ※는 규칙 엔진 값 그대로다.
다룰 주제마다 테스트 제안을 하나씩 만들어 팀원 조립 함수(agent.generate.assemble)에 넣으므로
결과 모양이 진짜 에이전트와 같다.
구분은 즉시 · 대기 · 협의 · 요청을 돌아가며 단다(화면에서 넷 다 보이게).
input_key는 비워 둔다 — 진짜 작성기가 직전 회차의 가짜 제안을 다시 쓰지 않게.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import cycle
from typing import Any

from agent import cite
from agent.generate import assemble
from agent.judgment import topics_of
from agent.prompt import CATEGORIES
from app.propose.base import LlmInput, WrittenItem

QUOTE = "테스트 원문입니다."
_CHUNK = {
    "chunk_id": "TEST-0001",
    "doc_name": "테스트 문서",
    "section": None,
    "page": None,
    "origin": "테스트",
    "text": f"{QUOTE} 진짜 작성기를 붙이면 이 자리에 표준매뉴얼 · 법령 원문이 들어갑니다.",
}
TEST_CHUNK = _CHUNK | {"label": cite.label(_CHUNK)}  # 진짜 근거처럼 표기를 붙인다(retrieve.py)


@dataclass(frozen=True)
class FakeWriter:
    name: str = "fake"

    def write(self, llm_input: LlmInput) -> list[WrittenItem]:
        categories = cycle(CATEGORIES)
        return [_item(item, categories) for item in llm_input["items"]]


def _item(item: dict[str, Any], categories: Iterator[str]) -> WrittenItem:
    # 다룰 주제가 없는 항목(E6)은 진짜처럼 주제 없는 제안 하나 → 「대응 제안」 섹션
    topics = list(dict.fromkeys(t["topic"] for t in topics_of(item) if t["topic"])) or [None]
    proposals = [
        {
            "topic": topic,
            "category": next(categories),
            "text": f"[테스트] {topic or item.get('name', '')} 제안 문장입니다.",
            "quote": QUOTE,
            "quote_ok": True,
            "evidence": [TEST_CHUNK["chunk_id"]],
        }
        for topic in topics
    ]
    out = assemble(item, proposals, {TEST_CHUNK["chunk_id"]: TEST_CHUNK}, "ok")
    return out | {"input_key": None}
