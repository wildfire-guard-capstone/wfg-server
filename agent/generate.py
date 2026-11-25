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

from agent import calc, check, cite, judgment, parts, polish, prompt, retrieve, situate, situation
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


def assemble_parts(
    item: dict[str, Any],
    plist: list[parts.Part],
    final: dict[str, dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    state: str,
    facts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """소제목 형식(11/24 BLUF, 제품 C3) → 화면용 항목.

    sections = [현황(kind facts: 상황 · 상태 · 파악 fact 블록)] + 보이는 소제목마다 [kind part: 핵심 판단 근거(reason) →
    제안(paragraph, part 칸) → 판단 근거(basis, 이름표 뗀 문장) → 자세히 보기(detail, show_detail일 때만)] + [kind notes: ※ 안내
    — 화면 「누락된 정보」]. paragraph 블록은 예전 꼴에 part 칸만 더해 비교 · 어시스턴트 · 보고서가 그대로 읽는다.
    출처 목록(evidence)은 화면 번호 순서(제안 → 판단 근거)대로 처음 나온 차례."""
    sections: list[dict[str, Any]] = [{"title": "", "kind": "facts", "blocks": fact_blocks(facts)}]
    cited: dict[str, None] = {}
    later: list[str] = []
    for p in plist:
        f = final.get(p.title) or {}
        props = f.get("proposals") or []
        if not f.get("show") or not props:
            continue
        blocks: list[dict[str, Any]] = []
        if f.get("reason"):
            blocks.append({"type": "reason", "text": f["reason"], "source": "llm"})
        blocks += [_para(x, by_id) | {"part": "" if p.untitled else p.title} for x in props]
        if f.get("basis"):
            blocks.append(
                {
                    "type": "basis",
                    "source": "llm",
                    "sentences": [
                        {
                            "kind": b["kind"],
                            "text": check.strip_label(b["text"]),
                            "evidence": b.get("evidence") or [],
                            "quote": b.get("quote", ""),
                            "quote_ok": b.get("quote_ok"),
                        }
                        for b in f["basis"]
                    ],
                }
            )
        if p.detail:
            # 자세히 보기는 늘 담고 보일지(show)만 LLM이 정한다 — 버전 비교(proposal_compare)가 규칙 엔진 자료를 LLM 선택과
            # 상관없이 세게 하려고. 화면은 show가 true일 때만 그린다
            blocks.append(
                {"type": "detail", "show": bool(f.get("show_detail")), "blocks": p.detail}
            )
        sections.append({"title": "" if p.untitled else p.title, "kind": "part", "blocks": blocks})
        cited |= dict.fromkeys(e for x in props for e in x.get("evidence") or [])
        later += [e for b in f.get("basis") or [] for e in b.get("evidence") or []]
    cited |= dict.fromkeys(later)
    if not any(s["kind"] == "part" for s in sections):
        sections[0]["blocks"].append({"type": "failed", "text": FAILED_TEXT})
    notes = parts.notes_of(item)
    if notes:
        sections.append({"title": "", "kind": "notes", "blocks": notes})
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
        parts: bool = False,
    ):
        """k = 검색 근거 수(C2 · C3 같음, D1). k_naive는 예전 이름(주면 k 대신 쓴다).
        parts = 11/24 소제목 형식(BLUF, 제품 C3 전용 — 앱 · 시범 실행이 켠다. C1 · C2와 평가 재현은 끈 채로 예전 흐름)."""
        if condition not in prompt.CONDITIONS:
            raise ValueError(f"condition은 {prompt.CONDITIONS} 가운데 하나: {condition}")
        if parts and condition != "C3":
            raise ValueError("소제목 형식(parts)은 제품 C3에서만 씁니다")
        self.llm, self.evidence, self.workers = llm, evidence, workers
        self.k = k_naive or k
        self.known_names = set(known_names)
        self.thinking = thinking
        self.condition = condition
        self.parts = parts

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
                # 제안 글에 넣은 근거 설명(「근거는 SM-p092-s01의 …」)은 지운다(표기 정리, 세 조건 공통)
                p["text"], cites = check.drop_cite_sentences(p["text"])
                if cites:
                    log.setdefault("cite_removed", []).append(cites)
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
        one = self._one_parts if self.parts else self._one
        with ThreadPoolExecutor(self.workers) as ex:
            res = list(
                ex.map(
                    lambda it: (
                        one(common, it, prev.get(it["id"]), calcs.get(it["id"]), pairs, by_id)
                        if self.parts
                        else one(common, it, prev.get(it["id"]), calcs.get(it["id"]), pairs)
                    ),
                    items,
                )
            )
        return [r[0] for r in res], [r[1] for r in res]

    # ── 11/24 소제목 형식(BLUF, 제품 C3) ─────────────────────────────────
    def prepare_parts(
        self,
        common: dict,
        item: dict,
        by_id: dict[str, dict],
        calc_lines: list[str] | None = None,
    ) -> tuple[list[dict], list[dict], dict[str, Any]]:
        """→ (보낼 메시지, 근거 청크, 기록 조각). 소제목 · 자세히 보기 · 관련 항목은 agent/parts.py, 검색은 소제목별."""
        judg = judgment.judgment_of(item)
        rule_topics = [t["topic"] for t in judg["topics"] if t["topic"]]
        flags = common.get("situation")
        kinds = judgment.kinds(judg)
        sit_line = situation.summary(flags) if flags else ""
        sits = situate.sentences(item, rule_topics)
        plist = parts.parts_of(item, by_id, common, prompt.scope_of(item["id"]))
        chunks, rlog = retrieve.search_parts(self.evidence, judg.get("name", ""), plist, kinds)
        related = parts.related_lines(item["id"], by_id)
        notes = [str(n.get("text", "")) for n in parts.notes_of(item) if n.get("text")]
        msgs = prompt.messages_parts(
            common,
            judg,
            chunks,
            plist,
            parts.detail_lines,
            related=related,
            codes=sorted(parts.codes_of(item, related)),
            notes=notes,
            situation_line=sit_line,
            value_sentences=list(sits.values()),
            calc=list(calc_lines or []),
            kinds=kinds,
        )
        part = {
            "judg": judg,
            "sits": sits,
            "calc": list(calc_lines or []),
            "kinds": kinds,
            "sit_line": sit_line,
            "plist": plist,
            "related": related,
            "retrieval": rlog | {"condition": "C3", "layout": "parts"},
        }
        return msgs, chunks, part

    def _clean_props(
        self,
        props: list[dict[str, Any]],
        reader: str,
        facts_text: str,
        by_id: dict,
        texts: dict,
        log: dict,
    ) -> None:
        """제안 표기 정리 · 인용 바로잡기(예전 흐름과 같은 단계)."""
        for p in props:
            p["text"] = check.strip_subject(check.tidy(p.get("text") or ""), reader)
            p["text"], cites = check.drop_cite_sentences(p["text"])
            if cites:
                log.setdefault("cite_removed", []).append(cites)
            p["text"] = check.drop_restated(p["text"], facts_text)
            p["category"] = check.fix_category(p["text"], p.get("category"))
            p["evidence"] = list(dict.fromkeys(e for e in p.get("evidence") or [] if e in by_id))
            fixed = fix_evidence(p, by_id)
            if fixed:
                log.setdefault("evidence_fixed", []).append(fixed)
            p["quote_ok"] = check.quote_ok(
                p.get("quote", ""), [texts.get(e, "") for e in p["evidence"]]
            )

    def _clean_basis(
        self,
        title: str,
        basis: list[dict[str, Any]],
        by_id: dict,
        texts: dict,
        allowed: tuple,
        judged_text: str,
        pairs: dict | None,
        log: dict,
        item_id: str = "",
        codes_ok: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
        """판단 근거 문장 검사(제안과 같은 검사, 사용자 11-24) → (남은 문장, 문제, 뺀 문장).
        「판정 값」 · 맺는 문장 = 값 대조(출처 없음), 「기준」 = 값 대조 + 발췌 ID + quote가 그 원문에 있는가.
        문장 속 발췌 ID · 출처 괄호 · 머리 기호는 먼저 지운다(tidy_basis — 그것 때문에 문장을 버리지 않음)."""
        kept, errs, removed = [], [], []
        name = f"「{title}」 " if title != parts.UNTITLED else ""
        for b in basis:
            raw = check.tidy_basis(b.get("text") or "")
            if not raw.strip(" ."):
                continue
            kind = check.basis_kind(raw)
            e = check.basis_sentence_errors(
                raw, allowed, judged_text, self.known_names, pairs, item_id, codes_ok
            )
            ev = [x for x in dict.fromkeys(b.get("evidence") or []) if x in by_id]
            quote, q_ok = b.get("quote") or "", None
            if kind == check.BASIS_RULE:
                if not ev:
                    e.append(
                        "기준 문장에 근거 발췌 ID(evidence)가 없습니다. 근거 발췌에 적힌 기준만 쓰고 그 ID를 다십시오"
                    )
                else:
                    tmp = {"quote": quote, "evidence": ev}
                    if fixed := fix_evidence(tmp, by_id):
                        log.setdefault("evidence_fixed", []).append(fixed | {"basis": title})
                    ev = tmp["evidence"]
                    q_ok = check.quote_ok(quote, [texts.get(x, "") for x in ev])
                    if not q_ok:
                        e.append(
                            "quote가 단 근거 원문에 없습니다. evidence로 단 발췌에서 기준을 적은 문장을 글자 그대로 옮기십시오"
                        )
            else:
                ev, quote = [], ""  # 판정 값 · 맺는 문장은 출처를 달지 않는다
            if e:
                errs.append(f"{name}판단 근거 문장 「{raw[:50]}」: {'; '.join(e)}")
                removed.append({"part": title, "text": raw, "errors": e})
                continue
            kept.append(
                {"kind": kind, "text": raw, "evidence": ev, "quote": quote, "quote_ok": q_ok}
            )
        return kept, errs, removed

    def _one_parts(
        self,
        common: dict,
        item: dict,
        prev: dict | None,
        calc_lines: list[str] | None = None,
        pairs: dict[str, Any] | None = None,
        by_id: dict[str, dict] | None = None,
    ) -> tuple[dict, dict]:
        """소제목 형식 항목 하나: 입력 → 검색 → EXAONE(현황 + 소제목) → 검사(현황 · 제안 · 판단 근거 · 핵심 판단 근거 ·
        다른 항목 번호) → (최대 2회 다시) → 조립."""
        by_items = by_id or {item["id"]: item}
        msgs, chunks, part = self.prepare_parts(common, item, by_items, calc_lines)
        judg, plist = part["judg"], part["plist"]
        titles = [p.title for p in plist]
        multi = len(plist) > 1
        key = input_key(msgs)
        log: dict[str, Any] = {
            "id": item["id"],
            "condition": "C3",
            "layout": "parts",
            "input_key": key,
            "attempts": [],
            "calc": part["calc"],
            "kinds": part["kinds"],
            "situations": part["sits"],
            "parts": [
                {"title": p.title, "detail": bool(p.detail), "query_words": p.query_words}
                for p in plist
            ],
            "related": part["related"],
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
        ev_by_id = {c["chunk_id"]: c for c in chunks}
        ids = list(ev_by_id)
        log["evidence"] = [(c["chunk_id"], c["label"], c.get("part", "")) for c in chunks]
        reader = common.get("reader", "")
        texts = {c["chunk_id"]: c["text"] for c in chunks}
        detail_text = "\n".join(ln for p in plist for b in p.detail for ln in parts.detail_lines(b))
        allowed = check.allowed_from(
            common,
            judg,
            "\n".join(part["calc"]),
            "\n".join(part["sits"].values()),
            prompt.scope_text(item["id"]),
            part["sit_line"],
            detail_text,
            "\n".join(part["related"]),
            *[c["text"] for c in chunks],
        )
        judged_text = judgment.text_of(judg, common)
        names = check.data_names(judg.get("facts"))
        flags = common.get("situation")
        base_at = str((common.get("proposal") or {}).get("base_at") or "")[-5:]
        now = (base_at, (flags or {}).get("now_ha")) if base_at and flags else None
        # 쓸 수 있는 다른 항목 번호 = [관련 항목의 일]의 번호 + 판정 한 줄 · 규칙 엔진 see가 가리키는 번호
        # (11/24 bluf-r4: E2 · E4가 E6 대신 「(S6)」을 씀 — 모든 번호를 받으면 못 잡는다)
        codes_ok = parts.codes_of(item, part["related"])
        approaching, all_near = check.approach_table(by_items)
        all_near = all_near and item["id"] in check.EVAC_ITEMS
        history: dict[str, set[str]] = {}
        best: dict[str, Any] | None = None
        schema = prompt.parts_schema(plist, ids)
        for attempt in range(1, ATTEMPTS + 1):
            try:
                raw = self.llm.chat(
                    msgs,
                    schema=schema,
                    thinking=self.thinking,
                    temperature=0.2 if attempt == 1 else 0.5,
                    max_tokens=10240
                    if self.thinking
                    else 8192,  # 소제목마다 판단 근거가 더해져 길다
                    tag=f"{item['id']}#{attempt}",
                )
            except Exception as e:
                log["attempts"].append(
                    {"raw": "", "errors": {"-": [f"LLM 호출 실패: {type(e).__name__}: {e}"]}}
                )
                continue
            try:
                out = check.parse_out_parts(raw, titles)
            except Exception as e:
                log["attempts"].append(
                    {"raw": raw[-3000:], "errors": {"-": [f"출력 형식 오류: {e}"]}}
                )
                continue
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
            fact_errs = [
                e.replace("판단 문장", "파악 문장").replace("판단 문단", "파악 문단")
                for e in fact_errs
            ]
            # 항목 번호는 제안 · 판단 근거에만(11/24 bluf-r4 E4 상태: 「도로 통제 요청이 이미 (S6) 포함되어 있다」)
            for step in FACT_STEPS:
                keep = []
                for s in check.split_sentences(facts.get(step) or ""):
                    if check.ITEM_REF.search(s):
                        msg = "항목 번호(「(E6)」 같은 것)는 현황 문단에 쓰지 말고 제안 문장에서만 쓰십시오"
                        fact_errs.append(f"{prompt.STEP_NAMES[step]} 문장 「{s[:60]}」: {msg}")
                        removed.append({"step": step, "text": s, "errors": [msg]})
                        continue
                    keep.append(s)
                facts[step] = " ".join(keep)
            facts_text = " ".join(facts.values())
            # 소제목별: show가 false면 내용을 버린다(사용자 11-24: 소제목 나타남 = LLM True/False)
            flat: list[tuple[str, int, dict[str, Any]]] = []
            hidden_filled = []
            for t in titles:
                po = out["parts"][t]
                if multi and not po["show"]:
                    if po["proposals"] or po["basis"] or po["reason"].strip():
                        hidden_filled.append(t)
                    po["proposals"], po["basis"], po["reason"] = [], [], ""
                self._clean_props(po["proposals"], reader, facts_text, ev_by_id, texts, log)
                flat += [(t, j, p) for j, p in enumerate(po["proposals"])]
            if hidden_filled:
                log.setdefault("hidden_filled", []).append(hidden_filled)
            props = [p for _, _, p in flat]
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
                condition="C3",
            )
            for i, p in enumerate(props):
                if x := check.xref_errors(p.get("text") or "", item["id"], codes_ok):
                    bad.setdefault(i, []).extend(x)
                if x := check.scope_errors(p.get("text") or "", item["id"]):
                    bad.setdefault(i, []).extend(x)
                if x := check.touch_errors(p.get("text") or "", approaching, all_near):
                    bad.setdefault(i, []).extend(x)
                # 「대상만 바꿔 나눔」은 다시 쓰게만 한다(11/24 bluf-r4 E4: 대피소가 다른 두 제안 가운데 하나가 빠짐)
                if i in bad:
                    bad[i] = [
                        check.SOFT + e if e.startswith("같은 조치를 대상만 바꿔") else e
                        for e in bad[i]
                    ]
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
            # 판단 근거 · 핵심 판단 근거 · 소제목 묶음 검사
            final: dict[str, dict[str, Any]] = {}
            part_errs: list[str] = []
            basis_removed: list[dict[str, Any]] = []
            for t in titles:
                po = out["parts"][t]
                name = f"「{t}」 " if t != parts.UNTITLED else ""
                good_here = [
                    p
                    for (tt, _, p), i in zip(flat, range(len(flat)), strict=True)
                    if tt == t and not check.hard(bad.get(i, []))
                ]
                kept, berrs, brem = self._clean_basis(
                    t,
                    po["basis"],
                    ev_by_id,
                    texts,
                    allowed,
                    judged_text,
                    pairs,
                    log,
                    item["id"],
                    codes_ok,
                )
                part_errs += berrs
                basis_removed += brem
                reason = check.tidy_reason(po["reason"])
                rerrs = (
                    check.reason_errors(reason, allowed, judged_text, self.known_names, pairs)
                    + check.touch_errors(reason, approaching, all_near)
                    if reason
                    else []
                )
                if rerrs:
                    part_errs += [f"{name}핵심 판단 근거 「{reason[:50]}」: {'; '.join(rerrs)}"]
                    if check.hard(rerrs):
                        reason = ""
                show = po["show"] or not multi
                if show and not po["proposals"]:
                    part_errs.append(
                        check.SOFT
                        + f"{name}show가 true인데 제안이 없습니다. 할 일이 없으면 show를 false로 두십시오"
                    )
                if show and po["proposals"] and not po["reason"].strip():
                    part_errs.append(check.SOFT + f"{name}핵심 판단 근거(reason)가 비었습니다")
                if (
                    show
                    and po["proposals"]
                    and not any(b["kind"] == check.BASIS_VALUE for b in kept)
                ):
                    part_errs.append(
                        check.SOFT + f"{name}판단 근거 첫 문장(「판정 값: …」)이 없습니다"
                    )
                final[t] = {
                    "show": show,
                    "basis": kept,
                    "reason": reason,
                    "proposals": good_here,
                    "show_detail": po["show_detail"],
                }
            if multi and not any(out["parts"][t]["show"] for t in titles):
                part_errs.append(
                    "모든 소제목이 show false입니다. 할 일이 있는 소제목은 show를 true로 두십시오"
                )
            if check.hard(fact_errs):
                bad[FACT_KEY] = fact_errs
            elif fact_errs:
                log.setdefault("facts_soft", []).append(fact_errs)
            good_all = [p for t in titles for p in final[t]["proposals"]]
            real = [r for r in removed if not r.get("filler")]
            shown_text = " ".join(
                [
                    *facts.values(),
                    *(p.get("text") or "" for p in good_all),
                    *(b["text"] for t in titles for b in final[t]["basis"]),
                ]
            )
            mentions = len(check.data_values(shown_text, allowed, names))
            score = (
                bool(facts["situation"]),
                len(good_all),
                mentions,
                -len(real) - len(basis_removed),
            )
            labels = [
                f"{'「' + t + '」 ' if t != parts.UNTITLED else ''}제안 {j + 1}" for t, j, _ in flat
            ]
            log["attempts"].append(
                {
                    "raw": raw[-8000:],
                    "errors": {labels[i] if i >= 0 else str(i): e for i, e in bad.items()},
                    "part_errors": part_errs,
                    "facts": facts,
                    "facts_removed": removed,
                    "basis_removed": basis_removed,
                    "shows": {t: out["parts"][t]["show"] for t in titles},
                    "show_detail": {t: out["parts"][t]["show_detail"] for t in titles},
                    "mentions": mentions,
                }
            )
            if best is None or score >= best["score"]:
                best = {
                    "score": score,
                    "final": final,
                    "facts": facts,
                    "removed": removed,
                    "basis_removed": basis_removed,
                    "dropped": [
                        {"part": flat[i][0], "text": props[i].get("text"), "errors": e}
                        for i, e in bad.items()
                        if i >= 0 and check.hard(e)
                    ],
                }
            if not bad and not part_errs:
                break
            if attempt < ATTEMPTS:
                flat_errs = [
                    (f"{labels[i]}: {e}" if i >= 0 else e)
                    for i, errs in sorted(bad.items())
                    for e in errs
                ] + part_errs
                shown = [f"- {labels[i]}: {p.get('text')}" for i, p in enumerate(props)]
                msgs = msgs + [
                    {"role": "assistant", "content": raw.split("</think>")[-1].strip()},
                    prompt.parts_retry_message(flat_errs, shown),
                ]
        final = best["final"] if best else {}
        facts = dict(best["facts"]) if best else {}
        if best and not facts.get("situation") and item.get("finding"):
            facts["situation"] = check.tidy_fact(item["finding"])
            log["facts_fallback"] = "finding"
        polish.polish(item["id"], final, facts, polish.village_labels(by_items))
        log["dropped"] = best["dropped"] if best else []
        log["facts"] = facts
        log["facts_removed"] = best["removed"] if best else []
        log["basis_removed"] = best["basis_removed"] if best else []
        log["final_messages"] = msgs[len(log["messages"]) :]
        good = [
            p
            for t in titles
            for p in (final.get(t) or {}).get("proposals") or []
            if (final.get(t) or {}).get("show")
        ]
        state = (
            "failed"
            if not good
            else (
                "partial"
                if log["dropped"]
                or log["basis_removed"]
                or any(not r.get("filler") for r in log["facts_removed"])
                else "ok"
            )
        )
        out_item = assemble_parts(item, plist, final, ev_by_id, state, facts)
        return out_item | {"input_key": key, "proposals": good}, log
