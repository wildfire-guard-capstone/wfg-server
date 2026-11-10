"""에이전트 작성기: 규칙 엔진 결과 → 근거 검색(RAG) → EXAONE 제안 → 검사 → 조립(agent/generate.py).

LLM(vLLM) · 근거 서버는 GPU 서버에 있다. API 서버에서는 터널 주소(설정 LLM_URL · EVIDENCE_URL)로
부른다(deploy/README.md 「GPU 서버 연결」). 의성 시연(demo/uiseong/step3_agent.py)과 같은 흐름이고,
다른 점은 지명 목록을 지도 파일이 아니라 DB에서 받는다는 것뿐이다.

직전 회차 다시 쓰기(prev)는 아직 하지 않는다 — 항목마다 새로 쓴다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.generate import Agent
from agent.situation import flags
from app.propose.base import LlmInput, WrittenItem

CALL_FAILED = "LLM 호출 실패"  # agent.generate가 남기는 시도 오류의 머리말


class WriterUnavailable(RuntimeError):
    """LLM에 한 번도 닿지 못했다(터널 끊김 · vLLM 꺼짐).
    빈 제안서를 저장하지 않게 작업을 실패시킨다."""


@dataclass(frozen=True)
class AgentWriter:
    llm: Any  # agent.clients.LLM
    evidence: Any  # agent.clients.Evidence
    names: Callable[[], set[str]] = set  # 지명 목록(제안 문장의 이름 검사) — 쓸 때 부른다
    workers: int = 4
    name: str = "agent"

    def write(self, llm_input: LlmInput) -> list[WrittenItem]:
        # 상황 깃발(agent/situation.py): 항목별 작성 지침(agent/guides.py)을 고르는 데 쓴다
        situation = flags(llm_input["bundles"], llm_input["items"], llm_input["common"])
        common = llm_input["common"] | {"situation": situation}
        agent = Agent(self.llm, self.evidence, self.names(), workers=self.workers)
        items, logs = agent.run(common, llm_input["items"])
        if items and all(_never_reached(log) for log in logs):
            raise WriterUnavailable("LLM 서버에 닿지 못했습니다(터널 · vLLM 확인)")
        return [{k: v for k, v in it.items() if k != "proposals"} for it in items]


def _never_reached(log: dict[str, Any]) -> bool:
    attempts = log.get("attempts") or []
    return bool(attempts) and all(
        any(e.startswith(CALL_FAILED) for e in (a.get("errors") or {}).get("-", []))
        for a in attempts
    )
