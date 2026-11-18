# ruff: noqa: E501
"""대응 제안 생성: 항목마다 (조건별 입력) → 근거 검색 → EXAONE → 생성 뒤 단계 → (최대 2회 다시) → 조립.

    agent = Agent(LLM(url), Evidence(url), known_names, condition="C3")
    items, logs = agent.run(common, rule_items, prev)

- 11/17 네 단계: LLM이 한 항목을 상황(situation) → 상태(status) → 판단(judgment) → 제안(proposals)으로 쓴다.
- 11/19 평가 조건 재설계(「대응 제안 평가 조건 · 지표 재설계(20261118) v2」): 세 조건이 같은 흐름 · 같은 다시 쓰기 횟수 ·
  같은 온도를 쓰고, 입력 구획(prompt.messages)과 생성 뒤 단계만 조건에 따라 켜고 끈다(표 8).

  | 단계                                         | C1 | C2 | C3 |
  | 표기 정리 · 주어 지우기 · 구분 바로잡기         | ○  | ○  | ○  |
  | 되풀이 지우기(drop_restated · drop_repeated)   | ○  | ○  | ○  |
  | 인용 바로잡기(fix_evidence) · 근거 ID 검사      | —  | ○  | ○  |
  | 말투 검사 · 다시 쓰기(check.check_one 형식 부분) | ○  | ○  | ○  |
  | 값 대조 검사 · 다시 쓰기(check_facts · value_errors · 짝 · 「최소 N동」 · 값 잃음) | — | — | ○ |
  | 권한 검사                                     | 없앰(P1)        |

  C1 = 원자료 표만(검색 없음), C2 = 원자료 표 + 검색 상위 k개, C3(제품) = 규칙층 판정 + 같은 검색(질의에 판정 값을 더함).
  원자료 표 글은 common["raw_table"]으로 받는다(demo/uiseong/raw_table.py가 만든다). 판단 계산 값(calc.py) ·
  판정 값 문장(situate.py) · 상황 한 줄(situation.summary)은 규칙층이 만든 것이라 C3에만 들어간다.
- 화면 배치: 첫 섹션 = 상황 · 상태 · 판단 문단(fact 블록) → 제안 문단(paragraph 블록, LLM이 쓴 순서).
  C3는 그 아래 규칙 엔진 섹션의 목록 · 표 · ※(판정 자료)를 소제목과 함께 둔다(채점 제외, P5).
- 기록(logs)에 항목마다 질의 · 근거 · 보낸 메시지 전문 · 받은 답 원문 · 검사 결과가 남는다.
- 보낸 메시지가 직전 회차와 글자까지 같은 항목은 직전 제안을 다시 쓴다(11/7).
"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent import calc, check, cite, judgment, prompt, retrieve, situate, situation
from agent.clients import LLM, Evidence
from agent.prompt import FACT_STEPS

FAILED_TEXT = (
    "검사를 통과한 제안이 없어 이 항목의 제안은 표시하지 않았습니다(이벤트 로그: 근거 부족)."
)
ATTEMPTS = 3  # 처음 + 다시 쓰기 2회(세 조건 같음)
FACT_KEY = -2  # 검사 결과에서 상황 · 상태 · 판단 문제의 자리
# 다시 쓴 제안이 앞 시도의 같은 자리 제안보다 자료 값(이름 · 시각 · 수)을 이만큼 넘게 잃으면 한 번 더 쓰게 한다(C3, 값 대조의 일부)
# (11/18 eval1118 회차 4 S3: 응원 기지 8곳 이름을 쓴 제안이 「19:20」 하나로 걸리자 다시 쓴 글이 「응원 기지에 대해」로 바뀜)
LOST_MIN, LOST_SHARE = 3, 0.5
NO_ALLOWED: tuple[set[str], set[str], str] = (set(), set(), "")


def lost_values(
    props: list[dict[str, Any]],
    history: dict[str, set[str]],
    allowed: tuple[set[str], set[str], str],
    names: set[str],
) -> dict[int, list[str]]:
    """제안 번호 → 앞 시도의 같은 자리(번호) 제안에 있던 자료 값 가운데 이번에 사라진 것(많이 잃었을 때만)."""
    out: dict[int, list[str]] = {}
    for i, p in enumerate(props):
        before = history.get(str(i), set())
        if not before:
            continue
        gone = before - check.data_values(p.get("text") or "", allowed, names)
        if len(gone) >= LOST_MIN and len(gone) > LOST_SHARE * len(before):
            out[i] = sorted(gone)
    return out


def input_key(msgs: list[dict[str, str]]) -> str:
    """보낸 메시지(지시문 + 입력) 전문의 해시. 같으면 직전 회차 제안을 다시 쓴다."""
    raw = json.dumps(msgs, ensure_ascii=False, sort_keys=True)
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


def _para(p: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """제안 하나 → paragraph 블록. 여러 블록이 이어져 네 번째 문단(제안)이 된다."""
    chunks = [by_id[e] for e in p.get("evidence") or [] if e in by_id]
    return {
        "type": "paragraph",
        "category": p["category"],
        "text": with_citation(p["text"], chunks),
        "body": with_citation(p["text"], []),  # 화면 본문(근거 표기 없음) — 근거는 마우스를 올리면
        "cite": cite.render(chunks),
        "action_text": p["text"],
        "evidence": p.get("evidence") or [],
        "quote": p.get("quote", ""),
        "quote_ok": p.get("quote_ok"),
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
    with_rules: bool = True,
) -> dict[str, Any]:
    """네 단계 (+ C3 판정 자료) → 화면용 항목.

    첫 섹션(제목 없음) = 상황 · 상태 · 판단 fact 블록 → 제안 paragraph 블록(LLM이 쓴 순서).
    with_rules(C3)면 규칙 엔진 섹션의 목록 · 표 · ※(판정 자료)를 그 소제목(없으면 「판정 자료」)으로 그 아래에 둔다.
    C1 · C2는 규칙층을 쓰지 않으므로 판정 자료 · finding · withheld를 넣지 않는다."""
    paras = [_para(p, by_id) for p in proposals]
    rest_sections = []
    if with_rules:
        for s in item.get("sections", []):
            data = [
                {k: v for k, v in b.items() if k != "writer"}
                for b in s.get("blocks", [])
                if b.get("type") in judgment.DATA_BLOCKS
            ]
            if data:
                rest_sections.append({"title": s.get("title") or "판정 자료", "blocks": data})
    blocks = fact_blocks(facts) + paras
    if not proposals:
        blocks.append({"type": "failed", "text": FAILED_TEXT})
    sections = [{"title": "", "blocks": blocks}, *rest_sections]
    cited = dict.fromkeys(e for p in proposals for e in p.get("evidence") or [])
    return {
        "id": item["id"],
        "name": item.get("name", ""),
        "state": state,
        "finding": item.get("finding", "") if with_rules else "",
        "withheld": item.get("withheld", []) if with_rules else [],
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
    코드가 고친다. 어느 청크에도 없는 quote는 고치지 않는다. 11/19: C2 · C3 모두(D8).
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
        k: int = retrieve.K,
        thinking: bool = False,
        condition: str = "C3",
        k_naive: int | None = None,
    ):
        """k = 검색 근거 수(C2 · C3 같음, D1). k_naive는 예전 이름(주면 k 대신 쓴다)."""
        if condition not in prompt.CONDITIONS:
            raise ValueError(f"condition은 {prompt.CONDITIONS} 가운데 하나: {condition}")
        self.llm, self.evidence, self.workers = llm, evidence, workers
        self.k = k_naive or k
        self.known_names = set(known_names)
        self.thinking = thinking
        self.condition = condition

    @property
    def with_rules(self) -> bool:
        return self.condition == "C3"

    @property
    def with_evidence(self) -> bool:
        return self.condition != "C1"

    def _call(self, msgs, ids, attempt, tag):
        return self.llm.chat(
            msgs,
            schema=prompt.schema(ids, self.with_evidence),
            thinking=self.thinking,
            temperature=0.2
            if attempt == 1
            else 0.5,  # 다시 쓸 때 같은 답이 나오지 않게(세 조건 같음)
            # 11/17 네 단계: 상황 · 상태 · 판단 문단이 더해져 답이 길다(E4 · E6은 3,000토큰을 넘을 수 있음)
            max_tokens=6144 if self.thinking else 4096,
            tag=tag,
        )

    def prepare(
        self,
        common: dict,
        item: dict,
        calc_lines: list[str] | None = None,
    ) -> tuple[list[dict], list[dict], dict[str, Any]]:
        """→ (보낼 메시지, 근거 청크, 기록 조각). 검색은 여기서 한다(C2 · C3)."""
        judg = judgment.judgment_of(item)
        rule_topics = [t["topic"] for t in judg["topics"] if t["topic"]]
        flags = common.get("situation")
        kinds = judgment.kinds(judg) if self.with_rules else []
        sit_line = situation.summary(flags) if (flags and self.with_rules) else ""
        calc_lines = list(calc_lines or []) if self.with_rules else []
        sits = situate.sentences(item, rule_topics) if self.with_rules else {}
        if self.with_evidence:
            chunks, rlog = retrieve.search(
                self.evidence,
                judg.get("name", ""),
                prompt.scope_of(item["id"]),
                kinds if self.with_rules else None,
                k=self.k,
            )
        else:
            chunks, rlog = [], {"queries": [], "k": 0, "found": []}
        msgs = prompt.messages(
            common,
            judg,
            chunks,
            sit_line,
            list(sits.values()),
            calc_lines,
            condition=self.condition,
            raw_table=common.get("raw_table", ""),
            kinds=kinds,
        )
        part = {
            "judg": judg,
            "sits": sits,
            "calc": calc_lines,
            "kinds": kinds,
            "sit_line": sit_line,
            "retrieval": rlog | {"condition": self.condition},
        }
        return msgs, chunks, part

    def _one(
        self,
        common: dict,
        item: dict,
        prev: dict | None,
        calc_lines: list[str] | None = None,
        pairs: dict[str, Any] | None = None,
    ) -> tuple[dict, dict]:
        """pairs = check.pair_table(모든 항목): 대상 · 시각 짝 검사(C3만)."""
        msgs, chunks, part = self.prepare(common, item, calc_lines)
        judg = part["judg"]
        key = input_key(msgs)
        log: dict[str, Any] = {
            "id": item["id"],
            "condition": self.condition,
            "input_key": key,
            "attempts": [],
            "calc": part["calc"],
            "kinds": part["kinds"],
            "situations": part["sits"],
            "retrieval": part["retrieval"],
            "messages": msgs,
        }
        if (
            prev
            and prev.get("input_key") == key
            and prev.get("state") in ("ok", "partial", "reused")
        ):
            log["reused_from"] = prev.get("version")
            out = dict(prev["item"]) | {"state": "reused"}
            return out | {"input_key": key, "proposals": prev["proposals"]}, log
        by_id = {c["chunk_id"]: c for c in chunks}
        ids = list(by_id)
        log["evidence"] = [(c["chunk_id"], c["label"], c["origin"]) for c in chunks]
        reader = common.get("reader", "")
        texts = {c["chunk_id"]: c["text"] for c in chunks}
        if self.with_rules:
            allowed = check.allowed_from(
                common,
                judg,
                "\n".join(part["calc"]),
                "\n".join(part["sits"].values()),
                prompt.scope_text(item["id"]),
                part["sit_line"],
                *[c["text"] for c in chunks],
            )
            judged_text = judgment.text_of(judg, common)
            names = check.data_names(judg.get("facts"))
            # 지금 면적과 기준 시각(「14:50 무렵 48.5 ha」 같은 짝 오류를 잡는 데 쓴다)
            flags = common.get("situation")
            base_at = str((common.get("proposal") or {}).get("base_at") or "")[-5:]
            now = (base_at, (flags or {}).get("now_ha")) if base_at and flags else None
        else:
            allowed, judged_text, names, now, pairs = NO_ALLOWED, "", set(), None, None
        history: dict[str, set[str]] = {}  # 제안 번호 → 앞 시도들에서 그 자리 제안이 쓴 자료 값(C3)
        best: dict[str, Any] | None = None
        for attempt in range(1, ATTEMPTS + 1):
            try:
                raw = self._call(msgs, ids, attempt, f"{item['id']}#{attempt}")
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
            if self.with_rules:  # 값 대조(C3): 틀린 사실부 문장은 빼고 다시 쓰게 한다
                facts, fact_errs, removed = check.check_facts(
                    item["id"],
                    out,
                    allowed,
                    judged_text,
                    self.known_names,
                    now,
                    judg["withheld"],
                    pairs,
                )
            else:  # C1 · C2: 표기 정리만
                facts = {step: check.tidy_fact(out.get(step) or "") for step in FACT_STEPS}
                facts = {k: (v if v.strip(" .") else "") for k, v in facts.items()}
                fact_errs, removed = [], []
            facts_text = " ".join(facts.values())
            for p in props:
                p.pop("topic", None)
                p["text"] = check.strip_subject(check.tidy(p.get("text") or ""), reader)
                # 제안에 상황 · 상태 · 판단을 되풀이한 문장(할 일이 없고 사실부와 겹침)은 지운다(세 조건)
                p["text"] = check.drop_restated(p["text"], facts_text)
                p["category"] = check.fix_category(p["text"], p.get("category"))
                if self.with_evidence:
                    p["evidence"] = list(
                        dict.fromkeys(e for e in p.get("evidence") or [] if e in by_id)
                    )
                    fixed = fix_evidence(p, by_id)
                    if fixed:
                        log.setdefault("evidence_fixed", []).append(fixed)
                    p["quote_ok"] = check.quote_ok(
                        p.get("quote", ""), [texts.get(e, "") for e in p["evidence"]]
                    )
                else:
                    p["evidence"], p["quote"], p["quote_ok"] = [], "", None
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
                None,
                repeated=repeated,
                pairs=pairs,
                condition=self.condition,
            )
            if self.with_rules:
                # 앞 시도의 같은 자리 제안이 쓴 자료 값을 많이 잃었으면 다시 쓰게만 한다(SOFT — 제안은 빼지 않음)
                lost = lost_values(props, history, allowed, names)
                for i, gone in lost.items():
                    bad.setdefault(i, []).append(
                        check.SOFT
                        + f"앞서 쓴 이름 · 값({', '.join(gone[:8])})을 지웠습니다. 검사에 걸린 값만 고치고 이 이름 · 값은 다시 쓰십시오"
                    )
                if lost:
                    log.setdefault("values_lost", []).append(
                        {"attempt": attempt, "lost": {str(i): g for i, g in lost.items()}}
                    )
                for i, p in enumerate(props):
                    history[str(i)] = history.get(str(i), set()) | check.data_values(
                        p.get("text") or "", allowed, names
                    )
            # 사실부 문제는 문장을 뺀 것(hard)이 있을 때만 다시 쓰게 한다(C3). SOFT만 있으면 기록만 한다
            if check.hard(fact_errs):
                bad[FACT_KEY] = fact_errs
            elif fact_errs:
                log.setdefault("facts_soft", []).append(fact_errs)
            # 문체 문제(check.SOFT)는 다시 쓰게만 하고 제안 · 문장을 빼지 않는다
            good = [p for i, p in enumerate(props) if not check.hard(bad.get(i, []))]
            # 고르는 순서: 상황이 있는가 → 통과한 제안 수 → 자료 값 언급 수(C3) → 상황 · 상태 · 판단에서 뺀 문장이 적은가
            real = [r for r in removed if not r.get("filler")]  # 군말을 뺀 것은 문제로 치지 않는다
            shown = " ".join([*facts.values(), *(p.get("text") or "" for p in good)])
            mentions = len(check.data_values(shown, allowed, names)) if self.with_rules else 0
            score = (bool(facts["situation"]), len(good), mentions, -len(real))
            log["attempts"].append(
                {
                    "raw": raw[-6000:],
                    "errors": {str(i): e for i, e in bad.items()},
                    "facts": facts,
                    "facts_removed": removed,
                    "mentions": mentions,
                }
            )
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
                    prompt.retry_message(flat, props, self.condition),
                ]
        good = best["good"] if best else []
        facts = dict(best["facts"]) if best else {}
        if self.with_rules and best and not facts.get("situation") and item.get("finding"):
            facts["situation"] = check.tidy_fact(item["finding"])  # 상황을 못 쓰면 판정 한 줄로(C3)
            log["facts_fallback"] = "finding"
        log["dropped"] = best["dropped"] if best else []  # 끝까지 검사에 걸려 뺀 제안
        log["facts"] = facts
        log["facts_removed"] = (
            best["removed"] if best else []
        )  # 끝까지 값이 틀려 뺀 사실부 문장(C3)
        log["final_messages"] = msgs[len(log["messages"]) :]  # 다시 쓰기 지시(있으면)
        state = (
            "failed"
            if not good
            else (
                "partial"
                if log["dropped"] or any(not r.get("filler") for r in log["facts_removed"])
                else "ok"
            )
        )
        out_item = assemble(item, good, by_id, state, facts, self.with_rules)
        return out_item | {"input_key": key, "proposals": good}, log

    def run(
        self, common: dict, items: list[dict], prev: dict[str, dict] | None = None
    ) -> tuple[list, list]:
        """항목 순서대로 결과와 기록. prev = 직전 회차의 {항목 id: {input_key, proposals, state, item, version}}.
        판단 계산 값(calc.py)은 항목끼리 걸치므로(E5가 E4 대피소를 씀) 여기서 미리 만든다(C3만 씀)."""
        prev = prev or {}
        by_id = {it["id"]: it for it in items}
        calcs = {iid: calc.for_item(iid, by_id) for iid in by_id} if self.with_rules else {}
        # 대상 · 시각 짝 검사표(C3): 마을 · 시설 · 도로 · 송전선은 여러 항목에 걸쳐 나오므로 모든 항목에서 만든다
        base_at = str((common.get("proposal") or {}).get("base_at") or "")[-5:]
        pairs = check.pair_table(items, base_at) if self.with_rules else None
        with ThreadPoolExecutor(self.workers) as ex:
            res = list(
                ex.map(
                    lambda it: self._one(
                        common, it, prev.get(it["id"]), calcs.get(it["id"]), pairs
                    ),
                    items,
                )
            )
        return [r[0] for r in res], [r[1] for r in res]
