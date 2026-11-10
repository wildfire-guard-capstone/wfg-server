# ruff: noqa: E501
"""어시스턴트 입구: 입력 하나 → 답(ANSWER) · 정정 확인안(CORRECTION_PREVIEW) · 처리 불가(UNSUPPORTED).

    a = Assistant(ctx, llm=LLM(...), evidence=Evidence(...))   # llm=None이면 규칙만(fake 모드)
    out = a.handle("괴산리 대피 언제까지 해야 해?", item_code=None)

결과는 JSON으로 바로 쓸 수 있는 dict다. 저장 · 토큰 · HTTP는 앱(app/services/assistant.py)이 한다.
- 대상을 못 찾는 정정 · 종료 산불 정정은 `CorrectionError`(status · code · message)
- LLM 서버에 닿지 않으면 `AssistantUnavailable`(앱이 503 AI_UNAVAILABLE)
"""

import time
import urllib.error
from typing import Any

from assistant import answer, correction, intent
from assistant.context import Context
from assistant.correction import CorrectionError

MAX_INPUT = 2000


class AssistantUnavailable(Exception):
    """LLM · 근거 서버에 닿지 않음."""


class _Guarded:
    """LLM · 근거 서버 호출의 연결 오류를 AssistantUnavailable로 바꾼다(JSON 오류는 그대로)."""

    def __init__(self, inner: Any):
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        fn = getattr(self.inner, name)
        if not callable(fn):
            return fn

        def call(*a: Any, **kw: Any) -> Any:
            try:
                return fn(*a, **kw)
            except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
                raise AssistantUnavailable(str(exc)) from exc

        return call


class Assistant:
    def __init__(self, ctx: Context, llm: Any | None = None, evidence: Any | None = None):
        self.ctx = ctx
        self.llm = _Guarded(llm) if llm is not None else None
        self.evidence = _Guarded(evidence) if evidence is not None else None

    def handle(self, text: str, item_code: str | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text or len(text) > MAX_INPUT:
            raise CorrectionError(422, "VALIDATION_ERROR", f"입력은 1~{MAX_INPUT}자여야 합니다.")
        t0 = time.time()
        trace: dict[str, Any] = {}
        kind = intent.classify(text, self.llm, trace)
        trace["intent"] = kind
        if kind == "CORRECTION":
            out = correction.preview(self.ctx, text, self.llm, trace)
        elif kind == "OTHER":
            out = {
                "type": "ANSWER",
                "route": "GUIDE",
                "tools": [],
                "answer": answer.GUIDE_TEXT,
                "sentences": [],
                "citations": [],
                "insufficient": [],
                "no_evidence": False,
            }
        else:
            out = answer.answer(self.ctx, text, item_code, self.llm, self.evidence, trace)
        trace["seconds"] = round(time.time() - t0, 1)
        calls = getattr(getattr(self.llm, "inner", None), "calls", None)
        if calls is not None:
            trace["llm_calls"] = list(calls)
        return out | {"trace": trace}


__all__ = ["Assistant", "AssistantUnavailable", "CorrectionError"]
