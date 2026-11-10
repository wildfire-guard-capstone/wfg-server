# ruff: noqa: E501
"""질문에 답하기(F-29): 도구 고르기 → 도구 실행 → 답 쓰기 → 검사 → 조립.

- 도구 고르기: 규칙(핵심어 · 마을 이름 · 항목 코드)과 LLM(prompt.PLAN)을 합친다. 규칙이 잡은 도구는 LLM이 빠뜨려도
  넣는다(「헬기 몇 대」에 resources가 빠지는 일을 막음). 「이 항목 질문」(item_code)이면 그 항목을 꼭 넣는다.
- 답 쓰기: 상황 값은 D1…, 근거 문서는 [1]…로 번호를 붙여 LLM에 주고, 문장마다 출처 번호를 받는다(prompt.ANSWER).
  LLM이 없으면 상황 값 문장을 그대로 잇는 규칙 답(fake 모드 · 시험용).
- 검사(check.py)에 걸리면 1번 다시 쓰게 하고, 그래도 걸린 문장은 뺀다. 남는 문장이 없으면 「찾지 못했습니다」
  (UC-QA-01 E1, no_evidence = 이벤트 로그 「근거 부족」).
- 조립: 근거 문서는 실제로 단 것만 처음 나온 순서로 다시 1, 2 …를 매겨 문장 끝 마침표 앞에 「 [1]」로 붙인다.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from assistant import prompt
from assistant.check import (
    _shares,
    _words,
    check_answer,
    filter_insufficient,
    unasked_unknown,
)
from assistant.context import ITEM_NAMES, Context
from assistant.tools import TOOLS, Fact, ToolBox, ToolResult, josa

MAX_FACTS = 70
MAX_CHUNKS = 8
GUIDE_TEXT = (
    "산불 대응에 관한 질문(현재 상황 · 대응단계 · 대피 · 진화자원 · 제안서 내용 · 관련 법령과 매뉴얼)에 답할 수 있습니다. "
    "현장 상황이 바뀌었다면 「괴산리 대피 완료」 「헬기 2대 추가 투입」처럼 알려 주십시오."
)
NO_EVIDENCE_TEXT = "질문하신 내용에 관한 상황 자료나 근거 문서를 찾지 못해 답하지 않았습니다. 질문을 바꿔 다시 물어 주십시오."

# 핵심어 → 도구(규칙 계획)
KEYWORDS: list[tuple[str, str]] = [
    ("resources", r"헬기|헬리|차량|소방차|진화차|인력|진화대|대원|자원|투입|동원|응원|기지"),
    ("weather", r"바람|풍속|풍향|기상|날씨|기온|습도|일몰|해\s?지|일출|야간|밤에"),
    ("stage", r"단계|격상|지휘권|지휘|발령"),
    ("spread", r"면적|확산|번지|퍼지|커지|ha|헥타르|시간\s?뒤|방향"),
    ("villages", r"마을|위험구역|주민|대피\s?대상|몇\s?명"),
    ("shelters", r"대피소|대피\s?장소|임시\s?주거|수용"),
    ("facilities", r"요양|학교|취약|시설|송전|변전|문화유산|국가유산|사찰|주택|가옥"),
    ("roads", r"도로|통제|교통|국도|우회"),
    ("alerts", r"문자|방송|전파|알림"),
    ("agencies", r"소방서|경찰서|병원|의료기관|관계\s?기관"),
    ("changes", r"바뀐|변경|달라|차이|정정|직전"),
    ("proposal", r"제안서|요약|전체\s?제안"),
    ("overview", r"개요|현황|지금\s?상황|상황\s?어때|어떻게\s?돼"),
]
WHY = re.compile(r"왜|이유|근거|기준|법|규정|매뉴얼|조항|권한|절차|책임|누가|누구|할\s?수\s?있")
DO = re.compile(r"해야|하나요|할까|할\s?일|조치|어떻게\s?(?:하|대응)|무엇을|뭘|뭐\s?해")
# 「무엇을 해야 하나」 질문의 주제 → 그 일을 다루는 항목
ITEM_TOPICS: list[tuple[str, str]] = [
    ("S1", r"단계|격상|지휘권|인계"),
    ("S2", r"우선\s?순위|보호\s?대상|먼저\s?지켜"),
    ("S3", r"헬기|자원|동원|응원|투입"),
    ("S4", r"야간|주간|밤|낮|일몰"),
    ("S5", r"송전|변전|문화유산|국가유산|사찰|시설\s?보호"),
    ("S6", r"대책\s?회의|협조|관계\s?기관|유관\s?기관"),
    ("E1", r"위험\s?구역"),
    ("E2", r"대피\s?(?:순서|시점|명령|언제)|먼저\s?대피|대피시켜|대피해야"),
    ("E3", r"요양|취약\s?시설|학교|병원"),
    ("E4", r"대피소|대피\s?장소"),
    ("E5", r"도로|통제|교통"),
    ("E6", r"문자|방송|전파"),
]
CODE = re.compile(r"(?<![A-Za-z])([SsEe][1-7])(?!\d)")


@dataclass
class Plan:
    route: str = "STATUS"
    calls: list[tuple[str, str]] = field(default_factory=list)
    source: str = "rules"
    matched: bool = False  # 규칙이 핵심어 · 마을 · 항목을 잡았는가(산불 질문이라는 뜻)


def _add(calls: list[tuple[str, str]], tool: str, arg: str = "") -> None:
    if (tool, arg) not in calls:
        calls.append((tool, arg))


VILLAGE_WORD = re.compile(r"(?<![가-힣])([가-힣]{1,3}리)(?=[\s,·와과의에을를은는이가도]|$)")
NOT_VILLAGE_WORD = {
    "거리",
    "관리",
    "처리",
    "수리",
    "무리",
    "원리",
    "유리",
    "논리",
    "요리",
    "마무리",
}


def mentioned_villages(box: ToolBox, text: str) -> list[str]:
    """질문에 나온 마을 이름. 마을 목록에 있는 이름(「괴산」처럼 「리」를 뺀 꼴도) + 목록에 없어도 「○○리」 꼴."""
    names = {v["name"] for v in box.ctx.zone_villages() + box.ctx.villages if v.get("name")}
    found = [
        n
        for n in names
        if n in text
        or (
            len(n) >= 3
            and re.search(
                rf"{re.escape(n[:-1])}(?:\s?\d)?\s?(?:마을|리|주민|은|는|이|가|에|의)", text
            )
        )
    ]
    found += [m for m in VILLAGE_WORD.findall(text) if m not in NOT_VILLAGE_WORD and m not in found]
    return sorted(dict.fromkeys(found), key=lambda n: text.find(n[:-1]))


def plan_rules(box: ToolBox, text: str, item_code: str | None) -> Plan:
    plan = Plan(source="rules")
    codes = [c.upper() for c in CODE.findall(text) if c.upper() in ITEM_NAMES]
    if item_code:
        codes.insert(0, item_code)
    why, do = bool(WHY.search(text)), bool(DO.search(text))
    if do or why:
        codes += [code for code, pat in ITEM_TOPICS if re.search(pat, text)][:2]
    for code in list(dict.fromkeys(codes))[:2]:
        _add(plan.calls, "item", code)
    for name in mentioned_villages(box, text)[:2]:
        _add(plan.calls, "village", name)
    hits = [tool for tool, pat in KEYWORDS if re.search(pat, text)]
    plan.matched = bool(plan.calls or hits)
    for tool in hits[:3]:
        _add(plan.calls, tool)
    if why and box.evidence is not None:
        _add(plan.calls, "search", text[:60])
    if not plan.calls:
        _add(plan.calls, "overview")
        if box.evidence is not None:
            _add(plan.calls, "search", text[:60])
    plan.route = "PROPOSAL" if codes and (why or do) else "REGULATION" if why else "STATUS"
    return plan


def plan_llm(llm: Any, box: ToolBox, text: str, item_code: str | None, trace: dict) -> Plan | None:
    ctx = box.ctx
    items = [
        (i["item_code"], i.get("name") or ITEM_NAMES.get(i["item_code"], "")) for i in ctx.items
    ]
    villages = [f"{v.get('emd', '')} {v['name']}".strip() for v in ctx.zone_villages()]
    raw = llm.chat(
        prompt.plan_messages(text, item_code, items, villages),
        schema=prompt.plan_schema(),
        max_tokens=300,
        tag="plan",
    )
    try:
        data = prompt.loads(raw)
    except ValueError:
        trace["plan_llm_error"] = raw[:200]
        return None
    trace["plan_llm"] = data
    plan = Plan(route=data.get("route", "STATUS"), source="llm")
    for c in data.get("calls") or []:
        tool, arg = c.get("tool"), (c.get("arg") or "").strip()
        if tool not in TOOLS:
            continue
        if tool == "item":
            arg = arg.upper()[:2]
            if arg not in ITEM_NAMES:
                continue
        elif tool in ("village", "shelters") and arg:
            if not box.find_villages(arg) and arg.removesuffix("리") not in text:
                continue
        elif tool == "search":
            arg = arg or text[:60]
        elif not TOOLS[tool].arg:
            arg = ""
        _add(plan.calls, tool, arg)
    return plan


def merge(llm_plan: Plan | None, rules: Plan, item_code: str | None, limit: int = 5) -> Plan:
    if llm_plan is None:
        out = rules
    else:
        out = Plan(route=llm_plan.route, source="llm+rules")
        if llm_plan.route == "OUT_OF_SCOPE" and rules.matched:
            out.route = rules.route  # 핵심어가 잡히면 산불 질문으로 본다
        for c in llm_plan.calls + rules.calls:
            _add(out.calls, *c)
    if item_code and ("item", item_code) not in out.calls:
        out.calls.insert(0, ("item", item_code))
    # 항목 · 마을 · 검색을 앞에(답의 핵심), 나머지는 순서대로 — 넘치면 뒤를 자른다
    first = [c for c in out.calls if c[0] in ("item", "village", "search")]
    rest = [c for c in out.calls if c not in first]
    out.calls = (first + rest)[:limit]
    return out


# ── 재료 모으기 ──────────────────────────────────────────────────────


@dataclass
class Sources:
    results: list[ToolResult]
    facts: list[tuple[str, Fact]]  # (D번호, Fact)
    chunks: list[dict[str, Any]]  # 번호 = 위치 + 1


def gather(box: ToolBox, plan: Plan) -> Sources:
    results = [box.run(tool, arg) for tool, arg in plan.calls]
    seen_f: dict[str, Fact] = {}
    for r in results:
        for f in r.facts:
            seen_f.setdefault(f.ref, f)
    facts = [(f"D{i}", f) for i, f in enumerate(list(seen_f.values())[:MAX_FACTS], 1)]
    judged = [c for r in results for c in r.chunks if c.get("origin") != "검색"]
    searched = [c for r in results for c in r.chunks if c.get("origin") == "검색"]
    uniq: dict[str, dict[str, Any]] = {}
    for c in judged + searched:  # 판정 · 인용 근거 먼저, 검색은 뒤에(넘치면 검색부터 잘림)
        uniq.setdefault(c["chunk_id"], c)
    return Sources(results, facts, list(uniq.values())[:MAX_CHUNKS])


# ── 답 쓰기 ─────────────────────────────────────────────────────────


def write_llm(
    llm: Any, ctx: Context, text: str, item_code: str | None, src: Sources, trace: dict
) -> tuple[list[dict[str, Any]], list[str]]:
    facts = dict(src.facts)
    msgs = prompt.answer_messages(ctx.reader, text, item_code, src.facts, src.chunks, src.results)
    schema = prompt.answer_schema(len(src.facts), len(src.chunks))
    best: tuple[list[dict[str, Any]], list[str]] = ([], [])
    attempts = []
    # 「없다」고 말해도 되는 것: 질문이 물은 것 + 지정 항목의 빠진 정보(「이 항목은 왜?」에 그 항목의 빠진 정보는 답)
    asked = " ".join(
        [text]
        + [
            f.label
            for r in src.results
            if item_code and r.name == "item"
            for f in r.facts
            if "(빠진 정보)" in f.label
        ]
    )
    for attempt in range(2):
        raw = llm.chat(msgs, schema=schema, max_tokens=900, tag=f"answer{attempt}")
        try:
            out = prompt.loads(raw)
        except ValueError:
            attempts.append({"raw": raw[:300], "error": "JSON"})
            msgs = msgs + [
                {"role": "user", "content": "JSON 형식이 깨졌습니다. 같은 형식으로 다시 쓰십시오."}
            ]
            continue
        good, errs = check_answer(out, facts, src.chunks)
        unasked = [s["text"] for s in good if unasked_unknown(s["text"], asked)]
        good = [
            s for s in good if s["text"] not in unasked
        ]  # 오류가 아니라 조용히 뺀다(다시 묻지 않음)
        attempts.append({"out": out, "errors": errs} | ({"unasked": unasked} if unasked else {}))
        insufficient = filter_insufficient(out.get("insufficient") or [], asked, good)
        if len(good) > len(best[0]) or not attempt:
            best = (good, insufficient)
        if not errs:
            break
        msgs = msgs + [
            {"role": "assistant", "content": raw.split("</think>")[-1].strip()},
            prompt.answer_retry(errs, out.get("sentences") or []),
        ]
    trace["answer_attempts"] = attempts
    return best


def write_rules(
    src: Sources, limit: int = 5, question: str = ""
) -> tuple[list[dict[str, Any]], list[str]]:
    """LLM 없이: 도구 순서대로 상황 값 문장을 잇는다(빠진 정보 문장은 뒤로). 근거 문서는 이름만 알린다.

    question을 주면(LLM 답이 비었을 때 대신 내는 답) 이름이 질문과 겹치는 값을 앞에 둔다.
    그중 빠진 정보(「헬기 운항 가능 여부 정보는 없습니다」)가 맨 앞 — 물은 것이 없다는 말이 먼저 나오게.
    """
    by_ref = {f.ref: d for d, f in src.facts}
    q = _words(question) if question else set()
    asked: list[dict[str, Any]] = []
    n_missing = 0
    out: list[dict[str, Any]] = []
    later: list[dict[str, Any]] = []
    for r in src.results:
        for j, f in enumerate(r.facts):
            if f.ref not in by_ref:  # 상황 값 개수 상한(MAX_FACTS)에 잘린 것
                continue
            s = {"text": f.sentence().rstrip("."), "data": [by_ref[f.ref]], "docs": []}
            missing = "(빠진 정보)" in f.label
            if q and _shares(_words(f.label), q):
                if missing:
                    asked.insert(n_missing, s)
                    n_missing += 1
                else:
                    asked.append(s)
            elif j < 4:
                (later if missing else out).append(s)
    out = (asked + out + later)[: limit - (1 if src.chunks else 0)]
    if src.chunks:
        labels = " · ".join(dict.fromkeys(c.get("label") or c["chunk_id"] for c in src.chunks[:3]))
        out.append(
            {
                "text": f"관련 근거 문서는 {labels}입니다",
                "data": [],
                "docs": list(range(1, min(3, len(src.chunks)) + 1)),
            }
        )
    return out, []


def compose(
    sentences: list[dict[str, Any]], src: Sources, insufficient: list[str]
) -> dict[str, Any]:
    """문장 → 화면 답(근거 [k] 다시 매김) + citations + 문장별 상황 값 출처.

    표기가 같은 근거(같은 쪽의 조각 여러 개 · 고시 별표 두 쪽)는 번호 하나로 묶는다(11-09 평가: 「[1] p.76;
    [2] p.76; [3] p.76」). 묶인 조각은 citations의 chunk_ids · text(이어 붙임)에 남는다.
    """
    facts = dict(src.facts)
    label_k: dict[str, int] = {}
    groups: dict[int, list[dict[str, Any]]] = {}
    shown = []
    for s in sentences:
        ks: list[int] = []
        for old in s.get("docs") or []:
            c = src.chunks[old - 1]
            lab = c.get("label") or c["chunk_id"]
            if lab not in label_k:
                label_k[lab] = len(label_k) + 1
            k = label_k[lab]
            members = groups.setdefault(k, [])
            if c not in members:
                members.append(c)
            if k not in ks:
                ks.append(k)
        body = s["text"].rstrip(" .")
        mark = "".join(f"[{k}]" for k in ks)
        shown.append(
            {
                "text": f"{body} {mark}." if mark else f"{body}.",
                "citations": ks,
                "data_refs": [
                    {"ref": facts[d].ref, "label": facts[d].label, "value": facts[d].value}
                    for d in s.get("data") or []
                    if d in facts
                ],
            }
        )
    tail = [
        f"{josa(x.rstrip(' .'), '은', '는')} 자료와 근거 문서에서 찾지 못해 답하지 않았습니다."
        for x in insufficient[:2]
    ]
    citations = []
    for k, members in sorted(groups.items()):
        first = members[0]
        citations.append(
            {
                "k": k,
                "chunk_id": first["chunk_id"],
                "chunk_ids": [c["chunk_id"] for c in members],
                "label": first.get("label"),
                "doc_name": first.get("doc_name"),
                "section": first.get("section"),
                "page": first.get("page"),
                "origin": first.get("origin"),
                "text": "\n…\n".join(c.get("text", "") for c in members),
            }
        )
    return {
        "answer": " ".join([s["text"] for s in shown] + tail),
        "sentences": shown,
        "citations": citations,
        "insufficient": insufficient[:2],
    }


def answer(
    ctx: Context,
    text: str,
    item_code: str | None = None,
    llm: Any | None = None,
    evidence: Any | None = None,
    trace: dict | None = None,
) -> dict[str, Any]:
    trace = trace if trace is not None else {}
    box = ToolBox(ctx, evidence)
    item_code = item_code.upper() if item_code else None
    rules = plan_rules(box, text, item_code)
    llm_plan = plan_llm(llm, box, text, item_code, trace) if llm is not None else None
    plan = merge(llm_plan, rules, item_code)
    trace["plan"] = {"route": plan.route, "calls": plan.calls, "source": plan.source}
    base = {
        "type": "ANSWER",
        "route": plan.route,
        "tools": [{"tool": t, "arg": a} for t, a in plan.calls],
    }
    if plan.route == "OUT_OF_SCOPE":
        return base | {
            "answer": GUIDE_TEXT,
            "sentences": [],
            "citations": [],
            "insufficient": [],
            "no_evidence": False,
        }
    src = gather(box, plan)
    trace["sources"] = {"facts": len(src.facts), "chunks": [c["chunk_id"] for c in src.chunks]}
    if llm is not None:
        sentences, insufficient = write_llm(llm, ctx, text, item_code, src, trace)
        if not sentences and (src.facts or src.chunks):
            # 11-10 평가: 묻는 값(대피 시한 · 추가 대수)이 없으면 EXAONE이 아는 값까지 비우거나, 두 번 다 검사에서
            # 떨어짐 → 「찾지 못했습니다」 대신 상황 값 문장(질문과 겹치는 것 먼저)으로 답한다
            sentences, _ = write_rules(src, limit=4, question=text)
            insufficient = filter_insufficient(insufficient, text, sentences)
            trace["fallback"] = "rules"
    else:
        sentences, insufficient = write_rules(src)
    if not sentences:
        return base | {
            "answer": NO_EVIDENCE_TEXT,
            "sentences": [],
            "citations": [],
            "insufficient": insufficient[:2],
            "no_evidence": True,
        }
    return base | compose(sentences, src, insufficient) | {"no_evidence": False}
