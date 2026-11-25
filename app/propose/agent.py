"""에이전트 작성기: 규칙 엔진 결과 → 근거 검색(RAG) → EXAONE 제안 → 검사 → 조립(agent/generate.py).

LLM(vLLM) · 근거 서버는 GPU 서버에 있다. API 서버에서는 터널 주소(설정 LLM_URL · EVIDENCE_URL)로
부른다(deploy/README.md 「GPU 서버 연결」). 의성 시연(demo/uiseong/step3_agent.py)과 같은 흐름이고,
다른 점은 지명 목록을 지도 파일이 아니라 DB에서 받는다는 것뿐이다.

직전 제안서(prev)를 받으면 LLM에 보낼 입력이 그대로인 항목은 그 제안을 그대로 쓴다(state = reused).
상황 정정 뒤에만 넘긴다 — 값이 안 바뀐 항목까지 새로 쓰면 LLM 문장이 달라져 모두 「변경」이 된다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.generate import Agent
from agent.situation import flags
from app.propose.base import LlmInput, PrevItems, WrittenItem

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
    # 11/24 소제목 형식(BLUF): 제품은 켠다. 화면(wfg-client)이 새 블록
    # (reason · basis · detail)을 그려야 하므로
    # 화면 PR을 먼저 머지한다(11-18 PR #11과 같은 순서)
    parts: bool = True

    def write(self, llm_input: LlmInput, prev: PrevItems | None = None) -> list[WrittenItem]:
        # 상황 깃발(agent/situation.py): 규칙층 구획의 「이번 상황」 한 줄에 쓴다
        situation = flags(llm_input["bundles"], llm_input["items"], llm_input["common"])
        common = llm_input["common"] | {"situation": situation}
        agent = Agent(self.llm, self.evidence, self.names(), workers=self.workers, parts=self.parts)
        items, logs = agent.run(common, llm_input["items"], prev)
        # 그대로 쓴 항목은 LLM을 부르지 않았으므로 빼고 본다
        tried = [log for log in logs if not log.get("reused_from")]
        if tried and all(_never_reached(log) for log in tried):
            raise WriterUnavailable("LLM 서버에 닿지 못했습니다(터널 · vLLM 확인)")
        return [{k: v for k, v in it.items() if k != "proposals"} for it in items]


def _never_reached(log: dict[str, Any]) -> bool:
    attempts = log.get("attempts") or []
    return bool(attempts) and all(
        any(e.startswith(CALL_FAILED) for e in (a.get("errors") or {}).get("-", []))
        for a in attempts
    )
