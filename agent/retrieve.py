# ruff: noqa: E501
"""근거 검색(RAG): 항목마다 질의를 만들어 근거 서버(/search)에서 찾고, 규칙 엔진의 판정 근거(/fetch)를 붙인다.

- 판정 근거: 규칙 엔진 rule_basis = 그 판정(단계 · 구역 · 동원 범위 …)에 쓴 법령 · 매뉴얼. 판정을 설명하는 근거라
  그대로 가져온다(origin = 판정 근거).
- 검색: 질의 = ① 항목 범위(prompt.ITEM_SCOPE) ② 항목 이름 + 판정 요약 ③ 항목 범위 + 상황(밤 · 단계 · 읽는 사람).
  질의마다 상위 k개를 받아 점수가 높은 순으로 합치고 판정 근거와 겹치는 것은 뺀다(origin = 검색).
- 어떤 조치를 제안할지는 LLM이 이 근거에서 고른다(규칙 엔진은 정하지 않음).
"""

import json
import re
from typing import Any

from agent import cite
from agent.clients import Evidence


def situation(judgment: dict[str, Any], common: dict[str, Any]) -> str:
    facts = json.dumps(judgment.get("facts", {}), ensure_ascii=False)
    words = []
    if re.search(r'"(?:night|now_is_night)": true', facts) or "밤" in judgment.get("finding", ""):
        words.append("야간 산불 일몰 전")
    stage = common.get("current_stage")
    if stage:
        words.append(f"산불 {stage}")
    reader = common.get("reader", "")
    if re.search(r"도지사|시장|산림청장", reader) and "군수" not in reader:
        words.append(f"{reader} 통합지휘")
    return " ".join(words)


def queries(judgment: dict[str, Any], common: dict[str, Any], scope: str) -> list[str]:
    name = judgment.get("name", "")
    out = [scope or name, f"{name} {judgment.get('finding', '')}"[:300]]
    sit = situation(judgment, common)
    if sit:
        out.append(f"{scope or name} {sit}")
    return list(dict.fromkeys(q.strip() for q in out if q.strip()))


def _page_of(chunk: dict[str, Any]) -> str | None:
    m = re.match(r"SM-p(\d+)", chunk["chunk_id"])
    return m.group(1) if m else None


def retrieve(
    evidence: Evidence,
    item: dict[str, Any],
    judgment: dict[str, Any],
    common: dict[str, Any],
    scope: str,
    k: int = 8,
    k_each: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """→ (근거 청크 목록, 기록). 청크에는 label · origin · score를 붙인다."""
    basis_ids = list(dict.fromkeys((item.get("rule_basis") or []) + basis_from_data(item)))
    want = expand_pages(basis_ids)
    basis, _ = evidence.fetch(want) if want else ([], [])
    got = {c["chunk_id"] for c in basis}
    missing = [
        i for i in basis_ids if i not in got and not any(g.startswith(i + "-s") for g in got)
    ]
    qs = queries(judgment, common, scope)
    best: dict[str, dict[str, Any]] = {}
    for q in qs:
        for h in evidence.search(q, k=k_each, exclude=list(got)):
            if h["chunk_id"] in got:
                continue
            if h["chunk_id"] not in best or h["score"] > best[h["chunk_id"]]["score"]:
                best[h["chunk_id"]] = h | {"query": q}
    found = sorted(best.values(), key=lambda h: -h["score"])[:k]
    out = [c | {"origin": "판정 근거", "score": None} for c in basis] + [
        h | {"origin": "검색"} for h in found
    ]
    for c in out:
        c["label"] = cite.label(c)
    log = {
        "queries": qs,
        "basis": basis_ids,
        "missing": missing,
        "found": [(h["chunk_id"], round(h["score"], 4), h["query"]) for h in found],
    }
    return out, log


PAGE_ID = re.compile(r"^SM-p\d{3}$")
LABEL_IDS = {"고시 별표": ["NOTI-MOBL-t0-p1", "NOTI-MOBL-t0-p2"]}


def expand_pages(ids: list[str], parts: int = 9) -> list[str]:
    """쪽 청크(SM-p078) → 그 쪽의 조각 청크(SM-p078-s01 …). 조각마다 ID가 있어야 LLM이 어느 문장을 근거로 썼는지 가려진다.
    없는 조각 ID는 /fetch가 missing으로 돌려주고 버린다."""
    out = []
    for i in ids:
        out += [f"{i}-s{k:02d}" for k in range(1, parts + 1)] if PAGE_ID.match(i) else [i]
    return out


def basis_from_data(item: dict[str, Any]) -> list[str]:
    """조립기 표(S6 · E6 요청 표)의 「근거」 칸 = 규칙 엔진이 그 행을 판정한 근거 → 청크 ID(p.95 · 고시 별표)."""
    ids: list[str] = []
    for s in item.get("sections", []):
        for b in s.get("blocks", []):
            if b.get("type") != "table":
                continue
            for r in b.get("rows", []):
                lab = str(r.get("basis", "")) if isinstance(r, dict) else ""
                for part in re.split(r",\s*", lab):
                    part = part.strip()
                    if m := re.fullmatch(r"p\.(\d+(?:·\d+)*)", part):
                        ids += [f"SM-p{int(n):03d}" for n in m.group(1).split("·")]
                    ids += LABEL_IDS.get(part, [])
    return list(dict.fromkeys(ids))
