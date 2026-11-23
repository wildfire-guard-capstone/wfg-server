# ruff: noqa: E501
"""11/24 소제목 형식(BLUF, 제품 C3): 소제목 · 자세히 보기 · 관련 항목 · 스키마 · 검사 · 생성 흐름(가짜 LLM)."""

import json

import pytest
from rules_example import full_inputs

from agent import calc, check, parts, prompt
from agent.generate import Agent
from rules.llm_input import build_llm_input

INCIDENT = {"name": "의성군 안평면 산불 (2025-03-22)", "sigungu": "의성군", "emd": "안평면"}
CHUNK = {
    "chunk_id": "SM-p075-s01",
    "doc_id": "SM",
    "doc_name": "표준매뉴얼",
    "page": "75",
    "section": "p.75",
    "text": "위험구역 주민은 바로 대피시킨다",
}
REQ_HEAD = ["관련 기관", "세부 대상(기관명)", "대상", "시각", "항목"]


def _s6(rows):
    return {
        "id": "S6",
        "name": "유관기관 동원·협조 요청",
        "sections": [{"title": "", "blocks": [{"type": "table", "head": REQ_HEAD, "rows": rows}]}],
    }


S2 = {
    "id": "S2",
    "name": "진화 우선지역·보호대상·시설 보호",
    "facts": {},
    "sections": [
        {
            "title": "1. 진화 우선순위",
            "blocks": [
                {"type": "action", "id": "S2-a1"},
                {"type": "list", "rows": [{"text": "① 인명: 신월리"}]},
                {"type": "note", "text": "※ 군사시설 위치 정보가 없습니다."},
            ],
        },
        {"title": "2. 보호", "blocks": [{"type": "action", "id": "S2-a2", "see": "S6"}]},
    ],
}
ROW = {
    "group": "전력",
    "agency": "한국전력공사",
    "target": "송전선",
    "when": "5시간 안",
    "item": "S2",
}


@pytest.fixture(scope="module")
def engine():
    return build_llm_input(full_inputs(), INCIDENT)


def test_titles_follow_specs_and_engine_sections(engine):
    by_id = {it["id"]: it for it in engine["items"]}
    for it in engine["items"]:
        ps = parts.parts_of(it, by_id, engine["common"], prompt.scope_of(it["id"]))
        if it["id"] in parts.SPECS:
            have = parts.section_numbers(it)
            want = [
                s.title
                for s in parts.SPECS[it["id"]]
                if s.section is None or not have or s.section in have
            ]
            assert [p.title for p in ps] == want
        else:
            assert [p.title for p in ps] == [parts.UNTITLED]


def test_code_hides_subheading_when_engine_made_no_section():
    e3 = {
        "id": "E3",
        "facts": {},
        "sections": [
            {
                "title": "1. 우선 대피",
                "blocks": [{"type": "list", "rows": [{"text": "철파리: 요양원"}]}],
            },
            {"title": "3. 이송", "blocks": [{"type": "action", "id": "E3-a4", "see": "E6"}]},
        ],
    }
    ps = parts.parts_of(e3, {"E3": e3}, {}, "")
    assert [p.title for p in ps] == [
        "즉시 대피·이송",
        "이송 요청",
    ]  # 「2. 사전대피」가 없으면 사전대피·이송 준비도 없음
    assert ps[0].detail and not ps[1].detail


def test_related_items_and_request_rows_both_ways():
    by_id = {"S2": S2, "S6": _s6([ROW])}
    assert parts.related_lines("S2", by_id) == [
        "- S6 유관기관 동원·협조 요청: 한국전력공사 — 송전선, 5시간 안"
    ]
    assert parts.related_lines("S6", by_id) == [
        "- S2 진화 우선지역·보호대상·시설 보호: 한국전력공사 — 송전선, 5시간 안"
    ]
    first, protect = parts.parts_of(S2, by_id, {}, "")
    assert first.detail == [
        {"type": "list", "rows": [{"text": "① 인명: 신월리"}]}
    ]  # ※ 안내는 자세히 보기에 넣지 않음
    assert protect.detail[0]["rows"] == [
        ["전력", "한국전력공사", "송전선", "5시간 안"]
    ]  # 항목 칸은 뺀다
    assert "한국전력공사" in protect.query_words  # 질의에 이어진 기관을 더한다
    assert [n["text"] for n in parts.notes_of(S2)] == ["※ 군사시설 위치 정보가 없습니다."]


def test_shelter_table_shows_shared_overflow_and_two_candidate_shortage():
    e4 = {
        "facts": {
            "shelters": [
                {
                    "village": "가리",
                    "people": 100,
                    "zone": "burning",
                    "until": "지금",
                    "hub_sites": [
                        {"name": "A학교", "capacity": 120},
                        {"name": "B학교", "capacity": 500},
                    ],
                },
                {
                    "village": "나리",
                    "people": 80,
                    "zone": "p5",
                    "hub_sites": [{"name": "A학교", "capacity": 120}],
                },
                {
                    "village": "다리",
                    "people": 200,
                    "zone": "p5",
                    "hub_sites": [
                        {"name": "D센터", "capacity": 100},
                        {"name": "E센터", "capacity": 50},
                    ],
                },
            ]
        }
    }
    (t,) = calc.shelter_table(e4)
    assert t["caption"] == "첫 후보에 몰리는 인원"
    assert t["rows"] == [
        ["A학교(120명)", "가리 · 나리", "180명", "60명 넘침"],
        ["D센터(100명)", "다리", "200명", "100명 넘침"],
        ["D센터 + E센터(150명)", "다리(두 후보)", "200명", "50명 모자람"],
    ]


def test_schema_keys_are_titles_in_order_with_generation_order_fields():
    ps = [
        parts.Part("가 보호", "x", detail=[{"type": "list", "rows": []}]),
        parts.Part("나 통제", "y"),
    ]
    s = prompt.parts_schema(ps, ["SM-1"])
    assert list(s["properties"]) == ["order", "situation", "status", "judgment", "parts"]
    pp = s["properties"]["parts"]
    assert list(pp["properties"]) == pp["required"] == ["가 보호", "나 통제"]
    assert pp["additionalProperties"] is False
    assert list(pp["properties"]["가 보호"]["properties"]) == [
        "show",
        "basis",
        "reason",
        "proposals",
        "show_detail",
    ]
    assert list(pp["properties"]["나 통제"]["properties"]) == [
        "show",
        "basis",
        "reason",
        "proposals",
    ]
    one = prompt.parts_schema([parts.Part(parts.UNTITLED, "z")], ["SM-1"])["properties"]["parts"]
    only = one["properties"][parts.UNTITLED]
    assert "show" not in only["properties"] and only["properties"]["proposals"]["minItems"] == 1


def test_basis_labels_reason_and_item_codes():
    assert check.basis_kind("판정 값: 신월리.") == check.BASIS_VALUE
    assert check.basis_kind("기준: 주민을 대피시킵니다.") == check.BASIS_RULE
    assert check.basis_kind("그래서 대피를 제안했습니다.") == check.BASIS_NOTE
    assert check.strip_label("판정 값: 신월리 128명.") == "신월리 128명."  # 화면에서만 지운다
    assert check.tidy_reason("신월리에 불이 닿았으므로.") == "신월리에 불이 닿았으므로"
    ok = {"S6", "E6"}
    assert (
        check.xref_errors("한국전력공사 전류 차단 요청(S6)과 함께, 진화하십시오.", "S2", ok) == []
    )
    assert check.hard(check.xref_errors("(S2)와 함께 하십시오.", "S2", ok))  # 자기 번호
    assert check.hard(check.xref_errors("(E1)과 함께 하십시오.", "S2", ok))  # 입력에 없는 번호
    soft = check.xref_errors("세부 내용은 S6 참조하십시오.", "S2", ok)
    assert soft and not check.hard(soft)  # 넘기는 말은 다시 쓰게만


def test_prompt_has_subheadings_related_and_details(engine):
    by_id = {"S2": S2, "S6": _s6([ROW])}
    ps = parts.parts_of(S2, by_id, {}, "")
    msgs = prompt.messages_parts(
        {"reader": "의성군수"},
        {"id": "S2", "name": S2["name"], "facts": {}, "withheld": [], "do_not_state": []},
        [CHUNK | {"part": "시설 보호", "label": "p.75"}],
        ps,
        parts.detail_lines,
        related=parts.related_lines("S2", by_id),
        codes=sorted(parts.codes_of(S2, parts.related_lines("S2", by_id))),
        notes=["※ 군사시설 위치 정보가 없습니다."],
    )
    sys_text, user = msgs[0]["content"], msgs[1]["content"]
    assert "{EVIDENCE_RULE}" not in sys_text and "판정 값: " in sys_text
    assert "- 진화 우선지역·보호대상:" in user and "- 시설 보호:" in user
    assert "[관련 항목의 일" in user and "한국전력공사 — 송전선" in user
    assert "[자세히 보기 자료 — 시설 보호" in user and "[규칙층 안내" in user
    assert "검색: 시설 보호" in user
    assert "[쓸 수 있는 다른 항목 번호] S6" in user  # 관련 항목 · see(S2 → S6)
    assert user.index("[소제목") < user.index("[근거 발췌]")


class PartsLLM:
    """첫 소제목만 show true, 나머지는 false. 판단 근거에 출처가 맞는 기준 · 지어낸 기준을 함께 넣는다."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, schema=None, **kw):
        self.calls += 1
        specs = schema["properties"]["parts"]["properties"]
        out = {}
        for n, (title, spec) in enumerate(specs.items()):
            ev = spec["properties"]["proposals"]["items"]["properties"]["evidence"]["items"][
                "enum"
            ][0]
            po = {
                "basis": [
                    {"text": "판정 값: 신월리.", "evidence": [], "quote": ""},
                    {
                        "text": "기준: 위험구역 주민은 바로 대피시킵니다.",
                        "evidence": [ev],
                        "quote": "위험구역 주민은 바로 대피시킨다",
                    },
                    {
                        "text": "기준: 지어낸 기준을 따릅니다.",
                        "evidence": [ev],
                        "quote": "전혀 다른 문서의 아무 구절입니다",
                    },
                    {"text": "그래서 대피를 제안했습니다.", "evidence": [], "quote": ""},
                ],
                "reason": "신월리에 불이 닿았으므로.",
                "proposals": [
                    {
                        "category": "즉시",
                        "text": "주민을 대피시키십시오.",
                        "evidence": [ev],
                        "quote": "위험구역 주민은 바로 대피",
                    }
                ],
            }
            if "show" in spec["properties"]:
                po = {"show": n == 0} | po
            if "show_detail" in spec["properties"]:
                po["show_detail"] = True
            out[title] = po
        body = {
            "order": prompt.ORDER_PARTS,
            "situation": "신월리에 불이 닿았습니다.",
            "status": "",
            "judgment": "",
        }
        return json.dumps(body | {"parts": out}, ensure_ascii=False)


class FakeEvidence:
    def search(self, query, k=2, exclude=()):
        return [CHUNK | {"score": 0.7}]


def test_parts_flow_hides_false_subheading_and_drops_unsupported_rule_sentence():
    by_id = {"S2": S2, "S6": _s6([ROW])}
    llm = PartsLLM()
    agent = Agent(llm, FakeEvidence(), {"신월리"}, workers=1, parts=True)
    items, logs = agent.run(
        {"reader": "의성군수", "current_stage": "확산대응 1단계"}, [S2, by_id["S6"]]
    )
    s2 = items[0]
    kinds = [s.get("kind") for s in s2["sections"]]
    assert kinds == ["facts", "part", "notes"]  # 시설 보호(show false)는 안 나옴, ※ 안내는 맨 아래
    part = s2["sections"][1]
    assert part["title"] == "진화 우선지역·보호대상"
    types = [b["type"] for b in part["blocks"]]
    assert types == ["reason", "paragraph", "basis", "detail"]
    assert part["blocks"][0]["text"] == "신월리에 불이 닿았으므로"
    assert part["blocks"][1]["part"] == "진화 우선지역·보호대상"
    sents = part["blocks"][2]["sentences"]
    assert [s["text"] for s in sents] == [
        "신월리.",
        "위험구역 주민은 바로 대피시킵니다.",
        "그래서 대피를 제안했습니다.",
    ]
    assert [s["kind"] for s in sents] == [
        "value",
        "rule",
        "note",
    ]  # 이름표는 화면에서만 지움, 지어낸 기준은 뺌
    assert part["blocks"][3]["show"] is True
    assert s2["state"] == "partial" and logs[0]["basis_removed"]
    assert [e["chunk_id"] for e in s2["evidence"]] == ["SM-p075-s01"]
    assert llm.calls >= 2  # 지어낸 기준 · 숨긴 소제목에 쓴 내용 → 다시 쓰기
    s6 = items[1]
    assert [s.get("kind") for s in s6["sections"]] == ["facts", "part"]
    assert s6["sections"][1]["title"] == ""  # 소제목 없는 항목


def test_checks_fixed_after_bluf_r4():
    # 사실을 말하는 「대피하지 않았음을 확인하고」는 할 일 부정이 아니다(E2 급한 제안이 빠졌음)
    assert not check.NEG.search(
        "신월리 주민이 아직 대피하지 않았음을 확인하고 즉시 대피를 완료하도록 지시하십시오."
    )
    assert check.NEG.search("주민을 대피시키지 않습니다.")
    # 「의성군 안평면과 의성읍」의 「과」는 이음 조사(부서 이름 아님)
    allowed = check.allowed_from("의성군 안평면 의성읍")
    assert not check.value_errors(
        "의성군 안평면과 의성읍 주민을 대피시키십시오.", allowed, "", set()
    )
    assert check.value_errors("의성군 산림녹색과에 지시하십시오.", allowed, "", set())
    # 판단 근거의 발췌 ID · 출처 괄호 · 머리 기호는 지우고 문장은 살린다
    assert (
        check.tidy_basis(
            "기준: SM-p076-s03 (p.76 · 「산불 재난」 위기관리 표준매뉴얼 p.76 · ) 시군구 경계를 넘으면 대피소를 함께 씁니다."
        )
        == "기준: 시군구 경계를 넘으면 대피소를 함께 씁니다."
    )
    assert check.tidy_basis("기준: * 대형 산불(100ha 이상) 지휘 : 시·도지사.").startswith(
        "기준: 대형 산불"
    )
    assert check.tidy_basis("기준: SM-p076-s03") == ""
    assert (
        check.tidy_basis("기준: — 5시간 범위 내 도달 지역은 즉시 대피합니다.")
        == "기준: 5시간 범위 내 도달 지역은 즉시 대피합니다."
    )
    # 쓸 수 있는 번호 = 관련 항목 줄 머리 + 판정 한 줄 + see
    s6 = {"id": "S6", "finding": "요청 4건 · 단계 발령 협의(산림청장)는 S1", "sections": []}
    assert parts.codes_of(s6, ["- S2 진화 우선지역: 한국전력공사 — 송전선"]) == {"S1", "S2"}
    assert parts.codes_of(S2, []) == {"S6"}
    assert check.hard(check.xref_errors("한국도로공사 요청(S6)과 함께 통제하십시오.", "E4", {"E6"}))


def test_api_view_and_compare_keep_new_blocks():
    from app.services.proposal_compare import llm_texts, rows_of
    from app.services.proposal_view import sections_out

    sections = [
        {
            "title": "",
            "kind": "facts",
            "blocks": [{"type": "fact", "step": "situation", "text": "상황."}],
        },
        {
            "title": "시설 보호",
            "kind": "part",
            "blocks": [
                {"type": "reason", "text": "송전선이 5시간 안에 있으므로", "source": "llm"},
                {
                    "type": "basis",
                    "sentences": [
                        {
                            "kind": "rule",
                            "text": "기준 문장.",
                            "evidence": ["SM-1"],
                            "quote": "q",
                            "quote_ok": True,
                        }
                    ],
                },
                {
                    "type": "detail",
                    "show": False,
                    "blocks": [
                        {
                            "type": "table",
                            "head": ["관련 기관", "대상"],
                            "rows": [["한국전력공사", "송전선"]],
                            "caption": "S6 요청",
                        },
                        {"type": "list", "rows": [{"text": "① 인명"}]},
                    ],
                },
            ],
        },
    ]
    out = sections_out(sections)
    assert [s.get("kind") for s in out] == ["facts", "part"]
    reason, basis, detail = out[1]["blocks"]
    assert reason == {"type": "reason", "text": "송전선이 5시간 안에 있으므로", "source": "llm"}
    assert basis["sentences"] == [
        {"kind": "rule", "text": "기준 문장.", "evidence": ["SM-1"], "quote": "q"}
    ]  # quote_ok 숨김
    assert detail["show"] is False and detail["blocks"][0]["rows"] == [["한국전력공사", "송전선"]]
    assert detail["blocks"][0]["caption"] == "S6 요청"
    # 버전 비교: 자세히 보기 행은 LLM이 숨겨도 규칙 엔진 자료로 센다, 핵심 판단 근거 · 판단 근거는 LLM 글
    assert "한국전력공사 · 송전선" in rows_of(sections) and "① 인명" in rows_of(sections)
    assert llm_texts(sections) == ["송전선이 5시간 안에 있으므로", "기준 문장."]
