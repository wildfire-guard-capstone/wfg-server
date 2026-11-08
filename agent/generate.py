# ruff: noqa: E501
"""대응 제안 생성: 항목마다 판정 · 다룰 주제 → 근거 검색 → EXAONE → 검사 → (최대 2회 다시) → 조립.

    agent = Agent(LLM(url), Evidence(url), known_names)
    items, logs = agent.run(common, rule_items, prev)

- 규칙 엔진 항목(rule_items)에서 판정과 다룰 주제만 쓴다(judgment.py). 문장 · 주제별 근거 쪽은 없다.
- 무엇을 제안할지는 LLM이 근거 발췌에서 고른다. 제안마다 주제 · 근거 청크 ID · 근거 문장(quote)이 있고, 표기는 cite.py가 붙인다.
- 화면 배치는 시나리오 v3처럼 규칙 엔진 섹션 순서: 소제목 → 그 주제의 제안 문단 → 그 섹션의 목록 · 표 · ※.
- 기록(logs)에 항목마다 질의 · 근거 · 보낸 메시지 전문 · 받은 답 원문 · 검사 결과가 남는다(디버깅용).
- 입력(판정 · 상황)이 직전 회차와 같은 항목은 직전 제안을 다시 쓴다(11/7).
"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent import check, cite, judgment, prompt, retrieve
from agent.clients import LLM, Evidence

FAILED_TEXT = (
    "검사를 통과한 제안이 없어 이 항목의 제안은 표시하지 않았습니다(이벤트 로그: 근거 부족)."
)
ATTEMPTS = 3  # 처음 + 다시 쓰기 2회
OTHER = "그 밖의 제안"


def input_key(judg: dict[str, Any], common: dict[str, Any]) -> str:
    keep = {k: common.get(k) for k in ("reader", "current_stage", "commander")}
    raw = json.dumps({"j": judg, "c": keep}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def with_citation(text: str, chunks: list[dict[str, Any]], see: str | None = None) -> str:
    """「…하십시오.」 + 근거 → 「…하십시오 [p.79·95].」(+ 「세부 내용은 S6 참조.」)"""
    mark = cite.render(chunks)
    body = text.rstrip()
    if body.endswith("."):
        body = body[:-1]
    out = f"{body} {mark}." if mark else f"{body}."
    if see and "참조" not in out:
        out += f" 세부 내용은 {see} 참조."
    return out


def _para(
    p: dict[str, Any], by_id: dict[str, dict[str, Any]], tp: dict[str, Any] | None = None
) -> dict[str, Any]:
    chunks = [by_id[e] for e in p["evidence"] if e in by_id]
    return {
        "type": "paragraph",
        "category": p["category"],
        "text": with_citation(p["text"], chunks, (tp or {}).get("see")),
        "evidence": p["evidence"],
        "quote": p.get("quote", ""),
        "quote_ok": p.get("quote_ok"),
        "topic": p.get("topic"),
        "action": (tp or {}).get("action"),
        "source": "llm",
    }


def assemble(
    item: dict[str, Any],
    proposals: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    state: str,
) -> dict[str, Any]:
    """제안 + 판정 자료 → 화면용 항목(시나리오 v3 배치: 섹션마다 소제목 → 제안 문단 → 목록 · 표 · ※)."""
    topics = judgment.topics_of(item)
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for p in proposals:
        by_topic.setdefault(p.get("topic") or OTHER, []).append(p)
    used: set[int] = set()
    sections = []
    if not topics and proposals:  # 다룰 주제가 없는 항목(E6): 제안을 먼저
        sections.append({"title": "대응 제안", "blocks": [_para(p, by_id) for p in proposals]})
        used = {id(p) for p in proposals}
    for s in item.get("sections", []):
        blocks = []
        for b in s.get("blocks", []):
            if b.get("type") == "action":
                tp = next(t for t in topics if t["action"] == b["id"])
                for p in by_topic.get(tp["topic"], []):
                    if id(p) not in used:
                        blocks.append(_para(p, by_id, tp))
                        used.add(id(p))
            elif b.get("type") in judgment.DATA_BLOCKS:
                blocks.append({k: v for k, v in b.items() if k != "writer"})
        if blocks:
            sections.append({"title": s.get("title", ""), "blocks": blocks})
    rest = [p for p in proposals if id(p) not in used]
    if rest:
        sections.append({"title": OTHER, "blocks": [_para(p, by_id) for p in rest]})
    if not proposals:
        sections.insert(
            0, {"title": "대응 제안", "blocks": [{"type": "failed", "text": FAILED_TEXT}]}
        )
    cited = dict.fromkeys(e for p in proposals for e in p["evidence"])
    return {
        "id": item["id"],
        "name": item.get("name", ""),
        "state": state,
        "finding": item.get("finding", ""),
        "withheld": item.get("withheld", []),
        "sections": sections,
        "evidence": [
            {
                k: by_id[e].get(k)
                for k in ("chunk_id", "label", "doc_name", "section", "page", "origin", "text")
            }
            for e in cited
            if e in by_id
        ],
    }


def fix_evidence(p: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict | None:
    """quote가 단 근거가 아니라 같은 호출의 다른 청크에서 왔으면 evidence를 그 청크로 바로잡는다(기록을 남김).

    EXAONE은 맞는 문장을 옮기고도 ID를 옆 청크로 다는 일이 잦다(tune4: 257개 중 74개). quote가 출처를 알려 주므로
    코드가 고친다. 어느 청크에도 없는 quote는 고치지 않는다.
    """
    q = p.get("quote", "")
    if not q or check.quote_ok(q, [by_id[e]["text"] for e in p["evidence"]]):
        return None
    hits = [cid for cid, c in by_id.items() if check.quote_ok(q, [c["text"]])]
    if not hits:
        return None
    before = list(p["evidence"])
    p["evidence"] = hits[:2]
    return {"quote": q[:60], "from": before, "to": p["evidence"]}


class Agent:
    def __init__(
        self,
        llm: LLM,
        evidence: Evidence,
        known_names: set[str],
        workers: int = 4,
        k: int = 10,
        thinking: bool = False,
    ):
        self.llm, self.evidence, self.workers, self.k = llm, evidence, workers, k
        self.known_names = set(known_names)
        self.thinking = thinking

    def _call(self, msgs, ids, topics, attempt, tag):
        return self.llm.chat(
            msgs,
            schema=prompt.schema(ids, topics),
            thinking=self.thinking,
            temperature=0.2 if attempt == 1 else 0.5,  # 다시 쓸 때 같은 답이 나오지 않게
            max_tokens=4096 if self.thinking else 3000,
            tag=tag,
        )

    def _one(self, common: dict, item: dict, prev: dict | None) -> tuple[dict, dict]:
        judg = judgment.judgment_of(item)
        topics = [t["topic"] for t in judg["topics"] if t["topic"]]
        key = input_key(judg, common)
        log: dict[str, Any] = {"id": item["id"], "input_key": key, "attempts": []}
        if (
            prev
            and prev.get("input_key") == key
            and prev.get("state") in ("ok", "partial", "reused")
        ):
            log["reused_from"] = prev.get("version")
            out = dict(prev["item"]) | {"state": "reused"}
            return out | {"input_key": key, "proposals": prev["proposals"]}, log
        chunks, rlog = retrieve.retrieve(
            self.evidence, item, judg, common, prompt.scope_of(item["id"]), k=self.k
        )
        by_id = {c["chunk_id"]: c for c in chunks}
        ids = list(by_id)
        log |= {
            "retrieval": rlog,
            "evidence": [(c["chunk_id"], c["label"], c["origin"]) for c in chunks],
        }
        allowed = check.allowed_from(common, judg, *[c["text"] for c in chunks])
        judged_text = judgment.text_of(judg, common)
        texts = {c["chunk_id"]: c["text"] for c in chunks} | {"_data": " / ".join(judg["data"])}
        msgs = prompt.messages(common, judg, chunks, self.known_names)
        log["messages"] = msgs
        best: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        for attempt in range(1, ATTEMPTS + 1):
            try:
                raw = self._call(msgs, ids, topics, attempt, f"{item['id']}#{attempt}")
            except Exception as e:  # 시간 초과 · 연결 끊김: 그 시도만 실패
                log["attempts"].append(
                    {"raw": "", "errors": {"-": [f"LLM 호출 실패: {type(e).__name__}: {e}"]}}
                )
                continue
            try:
                props = check.parse(raw)
            except Exception as e:
                log["attempts"].append(
                    {"raw": raw[-3000:], "errors": {"-": [f"출력 형식 오류: {e}"]}}
                )
                continue
            for p in props:
                p["text"] = check.strip_subject(
                    check.tidy(p.get("text") or ""), common.get("reader", "")
                )
                p["category"] = check.fix_category(p["text"], p.get("category"))
                p["evidence"] = list(
                    dict.fromkeys(e for e in p.get("evidence") or [] if e in by_id)
                )
                fixed = fix_evidence(p, by_id)
                if fixed:
                    log.setdefault("evidence_fixed", []).append(fixed)
                p["quote_ok"] = check.quote_ok(
                    p.get("quote", ""), [texts.get(e, "") for e in p["evidence"]] + [texts["_data"]]
                )
            bad = check.check(
                item["id"],
                props,
                set(ids),
                allowed,
                judged_text,
                self.known_names,
                common.get("reader", ""),
                texts,
                topics,
            )
            good = [p for i, p in enumerate(props) if i not in bad]
            log["attempts"].append(
                {"raw": raw[-5000:], "errors": {str(i): e for i, e in bad.items()}}
            )
            if len(good) >= len(best):
                best = good
                dropped = [
                    {"text": props[i].get("text"), "errors": e} for i, e in bad.items() if i >= 0
                ]
            if not bad:
                break
            if attempt < ATTEMPTS:
                flat = [
                    (f"제안 {i + 1}: {e}" if i >= 0 else e)
                    for i, errs in sorted(bad.items())
                    for e in errs
                ]
                msgs = msgs + [
                    {"role": "assistant", "content": raw.split("</think>")[-1].strip()},
                    prompt.retry_message(flat, props),
                ]
        log["dropped"] = dropped  # 끝까지 검사에 걸려 뺀 제안(이벤트 로그 구분: 근거 부족)
        state = (
            "failed"
            if not best
            else (
                "partial"
                if dropped or any(t not in {p.get("topic") for p in best} for t in topics)
                else "ok"
            )
        )
        out = assemble(item, best, by_id, state)
        return out | {"input_key": key, "proposals": best}, log

    def run(
        self, common: dict, items: list[dict], prev: dict[str, dict] | None = None
    ) -> tuple[list, list]:
        """항목 순서대로 결과와 기록. prev = 직전 회차의 {항목 id: {input_key, proposals, state, item, version}}."""
        prev = prev or {}
        with ThreadPoolExecutor(self.workers) as ex:
            res = list(ex.map(lambda it: self._one(common, it, prev.get(it["id"])), items))
        return [r[0] for r in res], [r[1] for r in res]
