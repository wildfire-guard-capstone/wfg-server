# ruff: noqa: E501
"""근거 검색(RAG, 11/19 재설계 D1): C2 · C3가 같은 코드 · 같은 k로 근거 서버(/search)에서 찾는다.

- 질의 = 항목 이름 + 짧은 항목 범위(prompt.ITEM_SCOPE). C3(제품)는 규칙층 판정 값(닿는 대상 종류 · 시간대,
  judgment.kinds)을 질의에 더한다. C1은 검색하지 않는다.
- 예전 앵커 검색(규칙 엔진이 매뉴얼 쪽 ID를 지정해 가져오기: rule_basis · anchor_basis · 요청 표 근거 칸)과
  작성 지침 질의는 P1(프롬프트에 매뉴얼 지식을 넣지 않음)에 따라 없앴다. 어떤 조치를 제안할지는 LLM이 이 발췌에서 고른다.
"""

import re
from typing import Any

from agent import cite
from agent.clients import Evidence

K = 5  # C2 · C3 공통(재설계 D1)


def query(name: str, scope: str, extra: list[str] | None = None) -> str:
    """질의 하나: 항목 이름 + 항목 범위(+ C3 판정 값)."""
    words = [name, scope, *(extra or [])]
    return re.sub(r"\s+", " ", " ".join(w for w in words if w)).strip()


def search(
    evidence: Evidence, name: str, scope: str, extra: list[str] | None = None, k: int = K
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """→ (근거 청크 목록(검색 순위 순서), 기록). 청크에는 label · origin(검색) · rank · query를 붙인다."""
    q = query(name, scope, extra)
    hits = evidence.search(q, k=k) if q else []
    out = [h | {"origin": "검색", "query": q, "rank": i + 1} for i, h in enumerate(hits[:k])]
    for c in out:
        c["label"] = cite.label(c)
    log = {
        "queries": [q] if q else [],
        "k": k,
        "found": [(h["chunk_id"], round(h.get("score") or 0.0, 4), q) for h in out],
    }
    return out, log


PAGE_ID = re.compile(r"^SM-p\d{3}$")


def expand_pages(ids: list[str], parts: int = 9) -> list[str]:
    """쪽 청크(SM-p078) → 그 쪽의 조각 청크(SM-p078-s01 …). 어시스턴트(assistant/tools.py)가 쓴다.
    없는 조각 ID는 /fetch가 missing으로 돌려주고 버린다."""
    out = []
    for i in ids:
        out += [f"{i}-s{k:02d}" for k in range(1, parts + 1)] if PAGE_ID.match(i) else [i]
    return out
