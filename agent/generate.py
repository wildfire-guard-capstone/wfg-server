# ruff: noqa: E501
"""대응 제안 생성: 항목마다 판정 · 다룰 주제 → 근거 검색 → EXAONE → 검사 → (최대 2회 다시) → 조립.

    agent = Agent(LLM(url), Evidence(url), known_names)
    items, logs = agent.run(common, rule_items, prev)

- 11/17 네 단계: LLM이 한 항목을 상황(situation) → 상태(status) → 판단(judgment) → 제안(proposals)으로 쓴다.
  판단에 필요한 계산은 calc.py가, S1 ~ S3 판정 값 문장은 situate.py가 미리 만들어 프롬프트에 넣는다.
  상황 · 상태 · 판단은 문장마다 값을 검사해 틀린 문장만 빼고(check.check_facts), 제안은 예전처럼 검사한다.
- 규칙 엔진 항목(rule_items)에서 판정과 다룰 주제만 쓴다(judgment.py). 문장 · 주제별 근거 쪽은 없다.
- 무엇을 제안할지는 LLM이 근거 발췌에서 고른다. 제안마다 주제 · 근거 청크 ID · 근거 문장(quote)이 있고, 표기는 cite.py가 붙인다.
- 화면 배치(11/17): 첫 섹션 = 상황 · 상태 · 판단 문단(fact 블록) → 제안 문단(paragraph 블록, 주제 순서),
  그 아래 규칙 엔진 섹션의 목록 · 표 · ※(판정 자료)를 소제목과 함께 둔다.
- 기록(logs)에 항목마다 질의 · 근거 · 보낸 메시지 전문 · 받은 답 원문 · 검사 결과가 남는다(디버깅용).
- 입력(판정 · 상황)이 직전 회차와 같은 항목은 직전 제안을 다시 쓴다(11/7).
"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent import calc, check, cite, guides, judgment, prompt, retrieve, situate, situation
from agent.clients import LLM, Evidence
from agent.prompt import FACT_STEPS

FAILED_TEXT = (
    "검사를 통과한 제안이 없어 이 항목의 제안은 표시하지 않았습니다(이벤트 로그: 근거 부족)."
)
ATTEMPTS = 3  # 처음 + 다시 쓰기 2회
FACT_KEY = -2  # 검사 결과에서 상황 · 상태 · 판단 문제의 자리(-1 = 빠진 주제)


def input_key(judg: dict[str, Any], common: dict[str, Any], guide: str = "") -> str:
    keep = {k: common.get(k) for k in ("reader", "current_stage", "commander")}
    raw = json.dumps({"j": judg, "c": keep, "g": guide}, ensure_ascii=False, sort_keys=True)
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
    """제안 하나 → paragraph 블록. 여러 블록이 이어져 네 번째 문단(제안)이 된다."""
    chunks = [by_id[e] for e in p["evidence"] if e in by_id]
    see = (tp or {}).get("see")
    return {
        "type": "paragraph",
        "category": p["category"],
        "text": with_citation(p["text"], chunks, see),
        "body": with_citation(
            p["text"], [], see
        ),  # 화면 본문(근거 표기 없음) — 근거는 마우스를 올리면
        "cite": cite.render(chunks),
        "action_text": p["text"],
        "evidence": p["evidence"],
        "quote": p.get("quote", ""),
        "quote_ok": p.get("quote_ok"),
        "topic": p.get("topic"),
        "action": (tp or {}).get("action"),
        "source": "llm",
    }


def fact_blocks(facts: dict[str, str] | None) -> list[dict[str, Any]]:
    """상황 · 상태 · 판단 → fact 블록(빈 단계는 뺀다). step = situation · status · judgment."""
    return [
        {"type": "fact", "step": step, "text": facts[step], "source": "llm"}
        for step in FACT_STEPS
        if facts and (facts.get(step) or "").strip()
    ]


def assemble(
    item: dict[str, Any],
    proposals: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    state: str,
    facts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """네 단계 + 판정 자료 → 화면용 항목(11/17).

    첫 섹션(제목 없음) = 상황 · 상태 · 판단 fact 블록 → 제안 paragraph 블록(다룰 주제 순서, 덧붙인 주제는 뒤).
    그 아래 = 규칙 엔진 섹션마다 목록 · 표 · ※(판정 자료)를 그 소제목으로. 소제목이 없으면 「판정 자료」."""
    topics = judgment.topics_of(item)
    order = {t["topic"]: i for i, t in enumerate(topics)}
    tp_of = {t["topic"]: t for t in topics}
    ordered = sorted(
        enumerate(proposals), key=lambda ip: (order.get(ip[1].get("topic"), len(order)), ip[0])
    )
    blocks = fact_blocks(facts) + [_para(p, by_id, tp_of.get(p.get("topic"))) for _, p in ordered]
    if not proposals:
        blocks.append({"type": "failed", "text": FAILED_TEXT})
    sections = [{"title": "", "blocks": blocks}]
    for s in item.get("sections", []):
        data = [
            {k: v for k, v in b.items() if k != "writer"}
            for b in s.get("blocks", [])
            if b.get("type") in judgment.DATA_BLOCKS
        ]
        if data:
            sections.append({"title": s.get("title") or "판정 자료", "blocks": data})
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

    def _call(self, msgs, ids, topics, attempt, tag, extras=None):
        return self.llm.chat(
            msgs,
            schema=prompt.schema(ids, topics, extras),
            thinking=self.thinking,
            temperature=0.2 if attempt == 1 else 0.5,  # 다시 쓸 때 같은 답이 나오지 않게
            # 11/17 네 단계: 상황 · 상태 · 판단 문단이 더해져 답이 길다(E4 · E6은 3,000토큰을 넘을 수 있음)
            max_tokens=6144 if self.thinking else 4096,
            tag=tag,
        )

    def _one(
        self, common: dict, item: dict, prev: dict | None, calc_lines: list[str] | None = None
    ) -> tuple[dict, dict]:
        judg = judgment.judgment_of(item)
        topics = [t["topic"] for t in judg["topics"] if t["topic"]]
        flags = common.get(
            "situation"
        )  # 상황 깃발(step3가 bundles로 만든다). 없으면 작성 지침 없이 예전대로
        guide = guides.render(item["id"], flags) if flags else ""
        extras = guides.extra_topics(item["id"], flags) if flags else None
        gqs = guides.queries(item["id"], flags) if flags else []
        sit_line = situation.summary(flags) if flags else ""
        calc_lines = list(calc_lines or [])
        key = input_key(judg, common, guide + "\n" + "\n".join(calc_lines))
        log: dict[str, Any] = {
            "id": item["id"],
            "input_key": key,
            "attempts": [],
            "guide": guide,
            "extras": extras,
            "calc": calc_lines,
        }
        if (
            prev
            and prev.get("input_key") == key
            and prev.get("state") in ("ok", "partial", "reused")
        ):
            log["reused_from"] = prev.get("version")
            out = dict(prev["item"]) | {"state": "reused"}
            return out | {"input_key": key, "proposals": prev["proposals"]}, log
        chunks, rlog = retrieve.retrieve(
            self.evidence,
            item,
            judg,
            common,
            prompt.scope_of(item["id"]),
            k=self.k,
            guide_queries=gqs,
        )
        by_id = {c["chunk_id"]: c for c in chunks}
        ids = list(by_id)
        log |= {
            "retrieval": rlog,
            "evidence": [(c["chunk_id"], c["label"], c["origin"]) for c in chunks],
        }
        reader = common.get("reader", "")
        # 지금 면적과 기준 시각(「14:50 무렵 48.5 ha」 같은 짝 오류를 잡는 데 쓴다)
        base_at = str((common.get("proposal") or {}).get("base_at") or "")[-5:]
        now = (base_at, (flags or {}).get("now_ha")) if base_at and flags else None
        # 판정 값 문장(S1 ~ S3, 코드) — 11/17부터는 문단 앞에 붙이지 않고 상황 · 상태 · 판단을 쓸 재료로 준다
        sits = situate.sentences(item, topics)
        log["situations"] = sits
        allowed = check.allowed_from(
            common,
            judg,
            "\n".join(calc_lines),
            "\n".join(sits.values()),
            prompt.scope_text(item["id"]),
            guide,
            *[c["text"] for c in chunks],
        )
        judged_text = judgment.text_of(judg, common)
        # 11/17: quote는 근거 원문과만 대조한다(판정 자료와 대조하면 매뉴얼에 없는 인용문이 통과했다)
        texts = {c["chunk_id"]: c["text"] for c in chunks}
        msgs = prompt.messages(
            common, judg, chunks, self.known_names, guide, sit_line, list(sits.values()), calc_lines
        )
        log["messages"] = msgs
        best: dict[str, Any] | None = None
        for attempt in range(1, ATTEMPTS + 1):
            try:
                raw = self._call(msgs, ids, topics, attempt, f"{item['id']}#{attempt}", extras)
            except Exception as e:  # 시간 초과 · 연결 끊김: 그 시도만 실패
                log["attempts"].append(
                    {"raw": "", "errors": {"-": [f"LLM 호출 실패: {type(e).__name__}: {e}"]}}
                )
                continue
            try:
                out = check.parse_out(raw)
            except Exception as e:
                log["attempts"].append(
                    {"raw": raw[-3000:], "errors": {"-": [f"출력 형식 오류: {e}"]}}
                )
                continue
            props = out["proposals"]
            facts, fact_errs, removed = check.check_facts(
                item["id"], out, allowed, judged_text, self.known_names, now, judg["withheld"]
            )
            facts_text = " ".join(facts.values())
            for p in props:
                p["text"] = check.strip_subject(check.tidy(p.get("text") or ""), reader)
                # 제안에 상황 · 상태 · 판단을 되풀이한 문장(할 일이 없고 사실부와 겹침)은 지운다
                p["text"] = check.drop_restated(p["text"], facts_text)
                p["category"] = check.fix_category(p["text"], p.get("category"))
                p["evidence"] = list(
                    dict.fromkeys(e for e in p.get("evidence") or [] if e in by_id)
                )
                fixed = fix_evidence(p, by_id)
                if fixed:
                    log.setdefault("evidence_fixed", []).append(fixed)
                p["quote_ok"] = check.quote_ok(
                    p.get("quote", ""), [texts.get(e, "") for e in p["evidence"]]
                )
            # 앞 제안과 거의 같은 문장은 뒤 제안에서 지운다(모두 겹치면 검사에서 다시 쓰게)
            repeated_out, repeated = check.drop_repeated(props)
            if repeated_out:
                log.setdefault("repeated_removed", []).append(repeated_out)
            bad = check.check(
                item["id"],
                props,
                set(ids),
                allowed,
                judged_text,
                self.known_names,
                reader,
                texts,
                topics,
                repeated=repeated,
            )
            # 사실부 문제는 문장을 뺀 것(hard)이 있을 때만 다시 쓰게 한다. 「등」 · 길이 같은 문체(SOFT)만 있으면
            # 기록만 한다(회차 1 2차: SOFT만으로 시도 3번을 다 써서 오히려 새 오류가 생김)
            if check.hard(fact_errs):
                bad[FACT_KEY] = fact_errs
            elif fact_errs:
                log.setdefault("facts_soft", []).append(fact_errs)
            # 문체 문제(check.SOFT)는 다시 쓰게만 하고 제안 · 문장을 빼지 않는다
            good = [p for i, p in enumerate(props) if i >= 0 and not check.hard(bad.get(i, []))]
            log["attempts"].append(
                {
                    "raw": raw[-6000:],
                    "errors": {str(i): e for i, e in bad.items()},
                    "facts": facts,
                    "facts_removed": removed,
                }
            )
            # 고르는 순서: 상황이 있는가 → 통과한 제안 수 → 상황 · 상태 · 판단에서 뺀 문장이 적은가
            real = [r for r in removed if not r.get("filler")]  # 군말을 뺀 것은 문제로 치지 않는다
            score = (bool(facts["situation"]), len(good), -len(real))
            if best is None or score >= best["score"]:
                best = {
                    "score": score,
                    "good": good,
                    "facts": facts,
                    "removed": removed,
                    "dropped": [
                        {"text": props[i].get("text"), "errors": e}
                        for i, e in bad.items()
                        if i >= 0 and check.hard(e)
                    ],
                }
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
        good = best["good"] if best else []
        facts = dict(best["facts"]) if best else {}
        if best and not facts.get("situation") and item.get("finding"):
            facts["situation"] = check.tidy_fact(item["finding"])  # 상황을 못 쓰면 판정 한 줄로
            log["facts_fallback"] = "finding"
        log["dropped"] = best["dropped"] if best else []  # 끝까지 검사에 걸려 뺀 제안(근거 부족)
        log["facts"] = facts
        log["facts_removed"] = best["removed"] if best else []  # 끝까지 값이 틀려 뺀 사실부 문장
        state = (
            "failed"
            if not good
            else (
                "partial"
                if log["dropped"]
                or any(not r.get("filler") for r in log["facts_removed"])
                or any(t not in {p.get("topic") for p in good} for t in topics)
                else "ok"
            )
        )
        out = assemble(item, good, by_id, state, facts)
        return out | {"input_key": key, "proposals": good}, log

    def run(
        self, common: dict, items: list[dict], prev: dict[str, dict] | None = None
    ) -> tuple[list, list]:
        """항목 순서대로 결과와 기록. prev = 직전 회차의 {항목 id: {input_key, proposals, state, item, version}}.
        판단 계산 값(calc.py)은 항목끼리 걸치므로(E5가 E4 대피소를 씀) 여기서 미리 만든다."""
        prev = prev or {}
        by_id = {it["id"]: it for it in items}
        calcs = {iid: calc.for_item(iid, by_id) for iid in by_id}
        with ThreadPoolExecutor(self.workers) as ex:
            res = list(
                ex.map(
                    lambda it: self._one(common, it, prev.get(it["id"]), calcs.get(it["id"])),
                    items,
                )
            )
        return [r[0] for r in res], [r[1] for r in res]
