"""대응 제안 생성 에이전트: 규칙 엔진의 LLM 입력(common + items) → 항목별 문단 → 조립된 제안서.

항목마다(동시에 여러 개):
  1. action 블록이 없으면(E6) LLM을 부르지 않는다
  2. 직전 제안서와 항목 입력이 같으면 직전 문단을 다시 쓴다(11/7 「변경 표시」)
  3. 근거: rule_basis ∪ anchor_basis를 ID로 가져오고, 항목 이름 + 주제로 보충 검색 2개
  4. EXAONE 호출 → 검사 → 실패하면 문제를 알려 1회 다시
  5. 그래도 실패하면 그 항목만 「문장 생성 실패」, 판정 값과 조립기 블록은 남긴다

추론 모드: 11/7 결정은 S2 · S4 · S5만 켜고 비교. 11/8 의성 데모에서 켜 보니 세 항목 모두 영어 추론이
4,096토큰을 넘겨(호출당 약 7.5분) JSON이 잘렸다 → 기본은 끄고 `thinking` 인자로 켤 수 있게 둔다.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent import check, prompt
from agent.clients import LLM, Evidence

THINKING_ITEMS: set[str] = set()  # 11/7 비교 대상: {"S2", "S4", "S5"}
FAILED_TEXT = "문장 생성 실패"


def _key(item: dict[str, Any]) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def assemble(item: dict[str, Any], paras: dict[str, dict] | None, state: str) -> dict[str, Any]:
    """문단 + 조립기 블록 → 제안서 항목. paras가 None이면 문단 자리에 실패 표시."""
    sections = []
    for s in item["sections"]:
        blocks = []
        for b in s["blocks"]:
            if b["type"] == "action":
                p = (paras or {}).get(b["id"])
                if p is None:
                    blocks.append(
                        {
                            "type": "failed",
                            "action": b["id"],
                            "topic": b.get("topic"),
                            "text": FAILED_TEXT,
                        }
                    )
                    continue
                text = check.tidy(p["text"])
                see = b.get("see")
                if see and f"{see} 참조" not in text:
                    text += f" 세부 내용은 {see} 참조."
                blocks.append(
                    {
                        "type": "paragraph",
                        "action": b["id"],
                        "category": p["category"],
                        "label": b.get("label"),
                        "topic": b.get("topic"),
                        "text": text,
                    }
                )
            else:
                blocks.append(b)
        sections.append({"title": s.get("title", ""), "blocks": blocks})
    keep = (
        "id",
        "name",
        "status",
        "finding",
        "withheld",
        "rule_basis",
        "anchor_basis",
        "targets",
        "do_not_state",
    )
    return {**{k: item.get(k) for k in keep}, "state": state, "sections": sections}


class Agent:
    def __init__(
        self,
        llm: LLM,
        evidence: Evidence,
        known_names: set[str],
        workers: int = 4,
        thinking: set[str] | None = None,
    ):
        self.llm, self.evidence, self.workers = llm, evidence, workers
        self.known_names = set(known_names) | prompt.STYLE_NAMES
        self.thinking = THINKING_ITEMS if thinking is None else set(thinking)

    def _one(self, common: dict, item: dict, prev: dict | None) -> tuple[dict, dict]:
        log: dict[str, Any] = {"id": item["id"], "attempts": []}
        acts = prompt.actions(item)
        if not acts:
            return assemble(item, {}, "no_llm"), log
        if prev and prev.get("input_key") == _key(item) and prev.get("state") in ("ok", "reused"):
            log["reused_from"] = prev.get("version")
            return assemble(item, prev["paras"], "reused") | {"paras": prev["paras"]}, log
        ids = list(dict.fromkeys((item.get("rule_basis") or []) + (item.get("anchor_basis") or [])))
        chunks, missing = self.evidence.fetch(ids)
        query = f"{item['name']} " + " ".join(a.get("topic", "") for a in acts)
        extra = self.evidence.search(query, k=2, exclude=ids)
        log |= {
            "evidence": [c["chunk_id"] for c in chunks],
            "missing": missing,
            "extra": [(c["chunk_id"], c["score"]) for c in extra],
        }
        allowed = check.allowed_from(
            common, item.get("finding"), item.get("facts"), *[c["text"] for c in chunks + extra]
        )
        msgs = prompt.messages(common, item, chunks, extra)
        thinking = item["id"] in self.thinking
        for attempt in (1, 2):
            try:
                raw = self.llm.chat(
                    msgs,
                    schema=prompt.schema(item),
                    thinking=thinking,
                    temperature=0.1 if attempt == 1 else 0.4,  # 다시 쓸 때 같은 답이 나오지 않게
                    max_tokens=4096 if thinking else 2000,
                    tag=f"{item['id']}#{attempt}",
                )
            except Exception as e:  # 시간 초과 · 연결 끊김: 그 시도만 실패로 친다
                log["attempts"].append(
                    {"errors": [f"LLM 호출 실패: {type(e).__name__}: {e}"], "raw": ""}
                )
                continue
            try:
                paras = check.parse(raw)
                for p in paras:  # 표기 정리(근거 위치 · 끝 마침표)는 검사 전에
                    p["text"] = check.tidy(p.get("text") or "")
                errors = check.check_item(item, paras, allowed, self.known_names)
            except Exception as e:  # JSON 깨짐
                paras, errors = [], [f"출력 형식 오류: {e}"]
            log["attempts"].append({"errors": errors, "raw": raw[-3000:]})
            if not errors:
                by = {p["action"]: p for p in paras}
                return assemble(item, by, "ok") | {"paras": by}, log
            msgs = msgs + [
                {"role": "assistant", "content": raw.split("</think>")[-1].strip()},
                prompt.retry_message(errors, paras),
            ]
        return assemble(item, None, "failed"), log

    def run(
        self, common: dict, items: list[dict], prev: dict[str, dict] | None = None
    ) -> tuple[list, list]:
        """항목 순서대로 결과와 기록.

        prev = 직전 제안서의 {항목 id: {input_key, paras, state, version}}.
        """
        prev = prev or {}
        with ThreadPoolExecutor(self.workers) as ex:
            futs = [ex.submit(self._one, common, it, prev.get(it["id"])) for it in items]
            done = [f.result() for f in futs]
        out = []
        for it, (res, _log) in zip(items, done, strict=True):
            res["input_key"] = _key(it)
            out.append(res)
        return out, [log for _, log in done]
