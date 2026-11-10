# ruff: noqa: E501
"""AI 어시스턴트(F-29 질의응답 · F-30 상황 정정) — 통합지휘권자의 입력 하나를 받아
답변 · 정정 확인안 · 처리 불가 안내 중 하나를 돌려준다.

    입력 ─ intent(질문 · 정정 · 그 밖) ─┬─ 질문: plan(도구 고르기) → tools(상황 값 · 제안 · 근거 검색)
                                        │        → answer(EXAONE 답 작성) → check(출처 · 숫자 · 이름) → 답
                                        └─ 정정: correction(해석 → 대상 찾기 · 허용 항목 · 범위 검사) → 확인안

- DB를 모른다. 앱(app/services/assistant.py)이나 데모(assistant/cli.py)가 `Context`를 만들어 넘긴다.
- 표준 라이브러리만 쓴다(agent/와 같이 GPU 서버에서도 그대로 돈다). LLM · 근거 서버는 agent.clients.
- 원칙(agent-spec 1장, DB 설계 2.5): 수치 · 판정은 규칙 엔진 값만 쓰고 바꾸지 않는다, 문장마다 출처
  (근거 문서 [k] 또는 상황 값)가 있어야 내보낸다, 질문은 상황을 바꾸지 않는다, 정정은 확인 전 저장하지 않는다.
"""

from assistant.context import Context
from assistant.service import Assistant

__all__ = ["Assistant", "Context"]
