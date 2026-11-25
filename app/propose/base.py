"""제안 작성기의 약속. 어떤 작성기든 같은 엔진 결과를 받아 같은 모양의 항목 결과를 돌려준다.

작성기(가짜 · 팀원 에이전트 …)는 이 약속만 지키면 갈아 끼울 수 있다.
작성기는 DB를 모른다 — 저장은 services가 한다.
항목 결과는 팀원 에이전트(agent/generate.py)가 내놓는 모양 그대로다(DB 설계 2.4 proposal_items).
"""

from typing import Any, Protocol

# rules.llm_input.build_llm_input 출력: {"bundles": 엔진 묶음, "common": 공통, "items": 항목 입력}
LlmInput = dict[str, Any]
# 항목 결과: id · name · state(ok · partial · failed · reused) · finding · withheld ·
# sections([{title, blocks: paragraph · list · table · note · failed}]) ·
# evidence([{chunk_id, label, doc_name, section, page, origin, text}]) · input_key
WrittenItem = dict[str, Any]
# 같은 예측의 직전 제안서 항목: {항목 id: {input_key, state, version, proposals, item}}
# (repositories.proposals.reusable_items). 입력이 그대로인 항목은 다시 쓰지 않는다(상황 정정 뒤)
PrevItems = dict[str, dict[str, Any]]


class ProposalWriter(Protocol):
    name: str

    def write(self, llm_input: LlmInput, prev: PrevItems | None = None) -> list[WrittenItem]: ...
