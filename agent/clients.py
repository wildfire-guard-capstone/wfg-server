"""바깥 서비스 두 개: 근거 검색 서버(rag-pipeline serve_evidence.py)와
LLM(vLLM OpenAI 호환, EXAONE).

둘 다 GPU 서버 127.0.0.1에서만 열린다. 표준 라이브러리(urllib)만 쓴다.
"""

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any


def _post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        json.dumps(body, ensure_ascii=False).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


@dataclass
class Evidence:
    url: str = "http://127.0.0.1:8032"
    timeout: float = 60

    def fetch(self, ids: list[str]) -> tuple[list[dict], list[str]]:
        if not ids:
            return [], []
        out = _post(f"{self.url}/fetch", {"ids": list(dict.fromkeys(ids))}, self.timeout)
        return out["chunks"], out["missing"]

    def search(self, query: str, k: int = 2, exclude: list[str] = ()) -> list[dict]:
        return _post(
            f"{self.url}/search", {"query": query, "k": k, "exclude": list(exclude)}, self.timeout
        )["hits"]


@dataclass
class LLM:
    url: str = "http://127.0.0.1:8031"
    model: str = "exaone-4.0-32b-awq"
    timeout: float = 900
    calls: list[dict] = field(default_factory=list)  # 호출 기록(토큰 · 시간)

    def chat(
        self,
        messages: list[dict],
        schema: dict | None = None,
        thinking: bool = False,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        tag: str = "",
    ) -> str:
        """한 번 부르고 답 글을 돌려준다. schema가 있으면 JSON 형식으로 묶는다
        (추론 모드에서는 묶지 않음).

        EXAONE 4.0: 추론 모드는 chat_template_kwargs.enable_thinking,
        답에는 <think> … </think>가 앞에 붙는다.
        """
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "top_p": 0.95,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": thinking},
        }
        if schema is not None and not thinking:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "out", "schema": schema},
            }
        t0 = time.time()
        out = _post(f"{self.url}/v1/chat/completions", body, self.timeout)
        text = out["choices"][0]["message"]["content"] or ""
        usage = out.get("usage", {})
        self.calls.append(
            {
                "tag": tag,
                "thinking": thinking,
                "seconds": round(time.time() - t0, 1),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "finish": out["choices"][0].get("finish_reason"),
            }
        )
        return text
