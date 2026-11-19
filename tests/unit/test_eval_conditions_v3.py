# ruff: noqa: E501
"""성능 평가 비교 조건(11/19 재설계, 「대응 제안 평가 조건 · 지표 재설계(20261118) v2」 5장):
C1 = 원자료 표만 · C2 = 원자료 표 + 단순 RAG · C3 = 규칙층 + 같은 RAG(제품). 가짜 LLM · 근거 서버.

① 세 조건 SYSTEM은 근거 규칙 한 줄 말고 글자까지 같다 ② 조건별 구획 있음/없음(표 7)
③ P1: 옛 작성 지침 · 요청 표 문장 · 의성 마을 이름이 프롬프트 고정 부분에 없다 ④ 생성 뒤 단계가 표 8대로 켜지고 꺼진다.
"""

import difflib
import json

import pytest
from rules_example import full_inputs

from agent import generate, judgment, prompt, retrieve
from agent.generate import Agent
from rules.items.requests import HEAD
from rules.llm_input import build_llm_input

ITEM = {
    "id": "E2",
    "name": "대피 대상·순서·시점",
    "finding": "위험구역 1곳 128명: 신월리 · 일몰 18:38",
    "facts": {"immediate": [{"name": "신월리", "people": 128, "night": False}]},
    "withheld": [{"field": "장애인 · 어린이 인원", "reason": "리 단위 자료 없음"}],
    "do_not_state": ["장애인 · 어린이 인원"],
    "targets": ["village:1"],
    "rule_basis": ["SM-p075"],
    "anchor_basis": ["SM-p075", "SM-p079"],
    "sections": [
        {
            "title": "1. 대피",
            "blocks": [
                {
                    "type": "action",
                    "id": "E2-a1",
                    "topic": "위험구역 마을 대피",
                    "anchors": ["p.75"],
                },
                {"type": "list", "writer": "조립기", "rows": [{"text": "지금: **신월리**(128명)"}]},
            ],
        }
    ],
}
ITEM_S4 = {
    "id": "S4",
    "name": "진화 방식·시간대 전략",
    "finding": "지금 낮 · 일몰 18:38",
    "facts": {"sun": {"sunset": "18:38", "now_is_night": False}},
    "withheld": [],
    "do_not_state": [],
    "sections": [],
}
RAW = "[원자료 표 — 시험]\n마을: 신월리 · 안평면 · 인구 128명 · 중심 36.40000, 128.60000"
COMMON = {
    "reader": "의성군수",
    "incident": {
        "name": "시험 산불",
        "address": "경북 의성군 안평면",
        "started_at": "2025-03-22 11:24",
    },
    "proposal": {"base_at": "2025-03-22 12:50", "weekday": "토요일"},
    "current_stage": "확산대응 1단계",
    "commander": {"now": "의성군수", "next": "경상북도지사"},
    "raw_table": RAW,
}
CHUNKS = {
    "SM-p075-s01": {
        "chunk_id": "SM-p075-s01",
        "doc_id": "SM",
        "doc_name": "표준매뉴얼",
        "page": "75",
        "section": "p.75",
        "text": "위험구역 주민은 바로 대피시킨다",
    },
    "SM-p079-s01": {
        "chunk_id": "SM-p079-s01",
        "doc_id": "SM",
        "doc_name": "표준매뉴얼",
        "page": "79",
        "section": "p.79",
        "text": "노약자와 환자를 먼저 대피시킨다",
    },
}
FACTS = {
    "situation": "신월리에는 이미 불이 닿았습니다.",
    "status": "신월리 주민은 128명입니다. 대피소는 3곳 모자랍니다.",  # 「3」은 판정에 없는 값
    "judgment": "",
}


class RecEvidence:
    """부른 기록을 남기는 가짜 근거 서버(검색 순위 순서로 돌려줌)."""

    def __init__(self):
        self.fetched, self.searched = [], []

    def fetch(self, ids):
        self.fetched.append(list(ids))
        return [CHUNKS[i] for i in ids if i in CHUNKS], [i for i in ids if i not in CHUNKS]

    def search(self, query, k=2, exclude=()):
        self.searched.append((query, k))
        return [CHUNKS["SM-p079-s01"] | {"score": 0.7}, CHUNKS["SM-p075-s01"] | {"score": 0.6}][:k]


class FakeLLM:
    def __init__(self, answers, facts=None):
        self.answers, self.calls, self.seen = list(answers), [], []
        self.facts = FACTS if facts is None else facts

    def chat(self, messages, schema=None, **kw):
        self.seen.append((messages, schema, kw))
        self.calls.append({"tag": kw.get("tag")})
        a = self.answers.pop(0) if self.answers else []
        return (
            a
            if isinstance(a, str)
            else json.dumps(self.facts | {"proposals": a}, ensure_ascii=False)
        )


def run(cond, answers, facts=None, names=("신월리", "석탑리"), item=ITEM, common=COMMON):
    ev, llm = RecEvidence(), FakeLLM(answers, facts)
    items, logs = Agent(llm, ev, set(names), workers=1, condition=cond).run(common, [item])
    return items[0], logs[0], llm, ev


# ── ① 지시문 ──────────────────────────────────────────────────────────────
def test_system_differs_only_in_evidence_rule_line():
    s = {c: prompt.system(c).splitlines() for c in prompt.CONDITIONS}
    assert s["C2"] == s["C3"]
    diff = [
        ln
        for ln in difflib.unified_diff(s["C1"], s["C3"], lineterm="", n=0)
        if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
    ]
    assert diff == ["-6. " + prompt.EVIDENCE_RULE["C1"], "+6. " + prompt.EVIDENCE_RULE["C3"]]
    assert prompt.SYSTEM == prompt.system("C3")
    with pytest.raises(ValueError):
        prompt.system("C4")


def test_schema_evidence_fields_only_with_rag():
    c1 = prompt.schema([], with_evidence=False)["properties"]["proposals"]
    assert set(c1["items"]["properties"]) == {"category", "text"}
    c3 = prompt.schema(["SM-p075-s01"])["properties"]["proposals"]
    assert set(c3["items"]["properties"]) == {"category", "text", "quote", "evidence"}
    # 11/19 P1: 「1 ~ 5개」 제한을 풀고 상한만(MAX_PROPOSALS = 10) — 세 조건 같음
    assert c1["minItems"] == c3["minItems"] == 1 and c1["maxItems"] == c3["maxItems"] == 10


# ── ② 구획(표 7) ───────────────────────────────────────────────────────────
SECTIONS = {
    "[읽는 사람]": ("C1", "C2", "C3"),
    "[산불 기본 정보]": ("C1", "C2", "C3"),
    "[없는 자료": ("C1", "C2", "C3"),
    "이번 항목: E2": ("C1", "C2", "C3"),
    "[다른 항목": ("C1", "C2", "C3"),
    "[원자료 표": ("C1", "C2"),
    "[규칙층 판정": ("C3",),
    "[판정 자료": ("C3",),
    "[근거 발췌]": ("C2", "C3"),
}


@pytest.mark.parametrize("cond", prompt.CONDITIONS)
def test_sections_by_condition(cond):
    j = judgment.judgment_of(ITEM)
    chunks = [CHUNKS["SM-p075-s01"] | {"label": "p.75", "origin": "검색"}]
    user = prompt.messages(
        COMMON, j, chunks, condition=cond, raw_table=RAW, kinds=["위험구역 마을"]
    )[1]["content"]
    for head, conds in SECTIONS.items():
        assert (head in user) == (cond in conds), (cond, head)
    # 단계 · 지휘권자는 판정이라 C3 구획에만(산불 기본 정보에는 없음)
    assert ("확산대응 1단계" in user) == (cond == "C3")
    assert ("경상북도지사" in user) == (cond == "C3")
    # 근거 발췌는 맨 끝(마지막 지시 바로 앞)
    if cond != "C1":
        assert user.index("[근거 발췌]") > max(
            user.index(h) for h in ("이번 항목: E2", "[다른 항목")
        )
        assert user.rstrip().endswith("근거 발췌에서 골라 JSON으로 쓰십시오.")
    else:
        assert user.rstrip().endswith("대응 제안을 JSON으로 쓰십시오.") and "근거 발췌" not in user


def test_baseline_needs_raw_table():
    j = judgment.judgment_of(ITEM)
    with pytest.raises(ValueError):
        prompt.messages(COMMON, j, [], condition="C1", raw_table="")
    prompt.messages(COMMON, j, [], condition="C3", raw_table="")  # 제품(C3)은 원자료를 받지 않는다


@pytest.mark.parametrize("cond", ("C1", "C2"))
def test_items_in_a_round_share_prefix_up_to_raw_table(cond):
    """접두 캐시: 지시문 → 읽는 사람 → 산불 기본 정보 → 없는 자료 → 원자료 표까지 항목마다 같다."""
    a = prompt.messages(COMMON, judgment.judgment_of(ITEM), [], condition=cond, raw_table=RAW)
    b = prompt.messages(COMMON, judgment.judgment_of(ITEM_S4), [], condition=cond, raw_table=RAW)
    assert a[0] == b[0]
    cut = a[1]["content"].index("이번 항목:")
    assert a[1]["content"][:cut] == b[1]["content"][:cut] and RAW in a[1]["content"][:cut]


# ── ③ P1: 프롬프트 고정 부분에 매뉴얼 지식 · 시나리오 값이 없다 ─────────────────
FIXED = [
    *(prompt.system(c) for c in prompt.CONDITIONS),
    *(" ".join(v) for v in prompt.ITEM_SCOPE.values()),
    *prompt.MISSING_DATA,
    prompt.RULE_HEAD,
    *prompt.LAST.values(),
    *(prompt.retry_message(["x"], [], c)["content"] for c in prompt.CONDITIONS),
]
# 옛 작성 지침(guides.py do · extra · q) · 요청 표 「요청 사항」 · 지시문 예문에 있던 매뉴얼 지식 조각
MANUAL_PHRASES = [
    "전류 차단",
    "우회선로",
    "한국전력공사",
    "한국도로공사",
    "국가유산청",
    "산림청장",
    "보건소장",
    "현장응급의료소",
    "마을순찰대",
    "재난안전법",
    "제40조",
    "시장 · 군수",
    "대피명령은",
    "소방차 배치",
    "경비 보호",
    "전문가 입회",
    "진화헬기 추가 투입",
    "교통통제",
    "우회도로 지정",
    "도로 이용객 대피",
    "주민대피 지원",
    "재난문자(CBS)",
    "자막방송",
    "주민 대피 안내와 이동 지원",
    "일몰 전",
    "공중 진화",
    "야간 지상",
    "안전장구",
    "위치 추적",
    "안전 교육",
    "철수 기준",
    "야간 사전대피",
    "안전취약계층 우선",
    "(5시간)",
    "(8시간)",
    "5~8시간",
    "인명 · 주택 · 국가기간시설",
    "재난안전대책본부",
    "통합지휘본부",
]
UISEONG = [
    "의성",
    "안평",
    "안계",
    "괴산리",
    "석탑리",
    "용기리",
    "신월리",
    "양곡리",
    "도덕리",
    "신안리",
    "박곡리",
]
SCENARIO_VALUES = ["14:50", "48.5", "3,695", "2,623", "18:38"]


@pytest.mark.parametrize("phrase", MANUAL_PHRASES + UISEONG + SCENARIO_VALUES)
def test_fixed_prompt_parts_have_no_manual_or_scenario_text(phrase):
    assert not any(phrase in t for t in FIXED), phrase


def test_guides_and_style_examples_are_gone():
    import importlib.util

    assert importlib.util.find_spec("agent.guides") is None
    assert not hasattr(prompt, "examples_of") and not hasattr(prompt, "authority")


def test_rule_layer_request_table_has_no_asks_or_pages():
    """D3: 요청 표에는 기관 · 대상 · 시각만. 「요청 사항」 · 「근거」 칸, 보건(이송 담당) 행이 없다."""
    llm_in = build_llm_input(full_inputs(), {"name": "시험", "sigungu": "의성군", "emd": "안평면"})
    assert "요청 사항" not in HEAD and "근거" not in HEAD
    tables = [
        b
        for it in llm_in["items"]
        for s in it["sections"]
        for b in s["blocks"]
        if b.get("type") == "table" and b.get("head") == HEAD
    ]
    assert tables
    for t in tables:
        for r in t["rows"]:
            assert set(r) == {"group", "agency", "target", "when", "item"}
            assert r["group"] != "보건"
    text = json.dumps([judgment.judgment_of(it) for it in llm_in["items"]], ensure_ascii=False)
    for phrase in (
        "전류 차단",
        "우회선로 확보",
        "소방차 배치",
        "거주자 이송을 맡을 기관",
        "교통통제, 우회도로",
    ):
        assert phrase not in text


# ── 검색(D1) ───────────────────────────────────────────────────────────────
def test_c2_c3_same_search_code_and_k_c3_adds_rule_terms():
    _, l2, _, e2 = run("C2", [[]])
    _, l3, _, e3 = run("C3", [[]])
    assert e2.fetched == e3.fetched == []  # 매뉴얼 쪽 지정 가져오기 없음
    (q2, k2), (q3, k3) = e2.searched[0], e3.searched[0]
    assert k2 == k3 == retrieve.K == 5 and len(e2.searched) == len(e3.searched) == 1
    assert q2 == "대피 대상·순서·시점 주민 대피 대상 · 순서 · 시점"
    assert q3 == q2 + " 위험구역 마을"  # C3만 판정된 대상 종류를 더함
    _, l1, _, e1 = run("C1", [[]])
    assert e1.searched == [] and e1.fetched == []


def test_kinds_from_facts():
    assert judgment.kinds(judgment.judgment_of(ITEM)) == ["위험구역 마을"]
    night = ITEM | {"facts": {"immediate": [], "pre_evac": [{"name": "박곡리", "night": True}]}}
    assert judgment.kinds(judgment.judgment_of(night)) == ["잠재 위험구역 마을", "야간"]


# ── ④ 생성 뒤 단계(표 8) ──────────────────────────────────────────────────
GOOD = {
    "category": "즉시",
    "text": "신월리 주민을 즉시 대피시키십시오.",
    "evidence": ["SM-p075-s01"],
    "quote": "위험구역 주민은 바로 대피",
}
UNKNOWN_VILLAGE = GOOD | {"text": "석탑리 주민을 즉시 대피시키십시오."}  # 판정에 없는 마을
WITH_ETC = GOOD | {"text": "신월리 주민 등을 즉시 대피시키십시오."}  # 말투 문제(「등」)
WRONG_ID = GOOD | {"evidence": ["SM-p079-s01"]}  # quote는 p.75 문장인데 p.79를 닮


def strip_ev(p):
    return {k: v for k, v in p.items() if k not in ("evidence", "quote")}


@pytest.mark.parametrize("cond", prompt.CONDITIONS)
def test_value_check_only_c3(cond):
    ans = UNKNOWN_VILLAGE if cond != "C1" else strip_ev(UNKNOWN_VILLAGE)
    it, lg, llm, _ = run(cond, [[ans]] * 3)
    if cond == "C3":  # 판정에 없는 마을 · 사실부 값(3곳) → 다시 쓰기 2번 → 끝까지 걸리면 뺀다
        assert len(llm.calls) == 3 and it["state"] == "failed"
        assert any(r["step"] == "status" for r in lg["facts_removed"])
    else:  # 값 대조가 없다: 한 번에 통과, 사실부도 그대로
        assert len(llm.calls) == 1 and it["state"] == "ok" and lg["facts_removed"] == []
        status = next(b for b in it["sections"][0]["blocks"] if b.get("step") == "status")
        assert "3곳 모자랍니다" in status["text"]


@pytest.mark.parametrize("cond", prompt.CONDITIONS)
def test_style_check_and_rewrite_all_conditions(cond):
    ok_facts = FACTS | {"status": "신월리 주민은 128명입니다."}
    bad = WITH_ETC if cond != "C1" else strip_ev(WITH_ETC)
    good = GOOD if cond != "C1" else strip_ev(GOOD)
    it, lg, llm, _ = run(cond, [[bad], [good]], facts=ok_facts)
    assert len(llm.calls) == 2 and it["state"] == "ok"  # 「등」으로 한 번 다시 씀(세 조건 같음)
    temps = [kw.get("temperature") for _, _, kw in llm.seen]
    assert temps == [0.2, 0.5]  # 다시 쓰기 온도도 세 조건 같다
    retry = llm.seen[1][0][-1]["content"]
    assert ("근거 발췌" in retry) == (cond != "C1")


@pytest.mark.parametrize("cond", ("C2", "C3"))
def test_fix_evidence_in_c2_and_c3(cond):
    ok_facts = FACTS | {"status": "신월리 주민은 128명입니다."}
    it, lg, _, _ = run(cond, [[WRONG_ID]], facts=ok_facts)
    para = next(b for b in it["sections"][0]["blocks"] if b["type"] == "paragraph")
    assert para["evidence"] == ["SM-p075-s01"] and para["quote_ok"] is True
    assert lg["evidence_fixed"][0]["to"] == ["SM-p075-s01"]


def test_c1_has_no_evidence_fields_or_rule_data():
    it, lg, llm, ev = run(
        "C1", [[strip_ev(GOOD)]], facts=FACTS | {"status": "신월리 주민은 128명입니다."}
    )
    schema = llm.seen[0][1]
    assert "evidence" not in schema["properties"]["proposals"]["items"]["properties"]
    para = next(b for b in it["sections"][0]["blocks"] if b["type"] == "paragraph")
    assert para["evidence"] == [] and para["cite"] == "" and it["evidence"] == []
    # C1 · C2는 규칙층을 쓰지 않으므로 판정 자료 섹션 · finding을 결과에 넣지 않는다
    assert len(it["sections"]) == 1 and it["finding"] == "" and it["withheld"] == []
    assert lg["calc"] == [] and lg["kinds"] == [] and lg["situations"] == {}


def test_no_authority_check_for_upper_reader():
    """권한 검사(대피명령은 시장 · 군수)를 없앴다(P1): 도지사가 읽어도 「대피명령을 발령하십시오」를 걸지 않는다."""
    common = COMMON | {"reader": "경상북도지사"}
    p = GOOD | {"text": "신월리에 대피명령을 발령하십시오."}
    it, _, llm, _ = run(
        "C3", [[p]], facts=FACTS | {"status": "신월리 주민은 128명입니다."}, common=common
    )
    assert len(llm.calls) == 1 and it["state"] == "ok"


def test_input_key_differs_by_condition_and_reuse_works():
    keys = {}
    for cond in prompt.CONDITIONS:
        it, *_ = run(cond, [[GOOD if cond != "C1" else strip_ev(GOOD)]])
        keys[cond] = it["input_key"]
    assert len(set(keys.values())) == 3
    msgs = [{"role": "user", "content": "a"}]
    assert generate.input_key(msgs) == generate.input_key(list(msgs))


def test_cite_sentences_dropped_in_all_conditions():
    """11-19 evalv3 파일럿: 제안 글에 「근거는 SM-p092-s01의 "…"」를 넣은 것(55개 중 46개)을 세 조건 모두 지운다."""
    from agent import check

    text = '신월리 주민을 즉시 대피시키십시오. 근거는 SM-p075-s01의 "위험구역 주민은 바로 대피시킨다"입니다.'
    out, removed = check.drop_cite_sentences(text)
    assert out == "신월리 주민을 즉시 대피시키십시오." and len(removed) == 1
    out2, _ = check.drop_cite_sentences("SM-p079-s01에 따라 노약자를 먼저 대피시키십시오.")
    assert out2 == "노약자를 먼저 대피시키십시오."
    only, removed3 = check.drop_cite_sentences(
        "근거는 p.75입니다."
    )  # 할 일이 없으면 그대로(검사가 다시 쓰게)
    assert only == "근거는 p.75입니다." and removed3 == []
    for cond in prompt.CONDITIONS:
        p = {"category": "즉시", "text": text}
        if cond != "C1":
            p |= {"evidence": ["SM-p075-s01"], "quote": "위험구역 주민은 바로 대피"}
        it, lg, _, _ = run(cond, [[p]], facts=FACTS | {"status": "신월리 주민은 128명입니다."})
        para = next(b for b in it["sections"][0]["blocks"] if b["type"] == "paragraph")
        assert "근거는" not in para["body"] and lg["cite_removed"]
    assert all("text에는 넣지 않습니다" in prompt.system(c) for c in prompt.CONDITIONS)


def test_invalid_condition():
    with pytest.raises(ValueError):
        Agent(FakeLLM([]), RecEvidence(), set(), condition="C4")
