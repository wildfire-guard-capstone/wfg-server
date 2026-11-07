"""에이전트 문장 검사 · 조립의 핵심 경우."""

from agent import check
from agent.generate import assemble

ITEM = {
    "id": "S1",
    "name": "대응단계",
    "finding": "현재 기준 1단계(피해면적 48.5 ha) · P5 기준 2단계(피해면적 363 ha)",
    "facts": {
        "now": {"area_ha": 48.5, "wind_ms": 3.4},
        "area": {"slices": [{"until": "14:50", "ha": 205}]},
    },
    "sections": [
        {
            "title": "1. 단계",
            "blocks": [
                {
                    "type": "action",
                    "id": "S1-a1",
                    "topic": "현재",
                    "use": ["now"],
                    "anchors": ["p.73", "p.91"],
                },
                {"type": "list", "writer": "조립기", "rows": [{"text": "**신월리**"}]},
                {
                    "type": "action",
                    "id": "S1-a2",
                    "topic": "지휘권",
                    "use": [],
                    "anchors": ["p.72", "시행령 제21조①"],
                    "see": "S6",
                },
            ],
        }
    ],
}
ALLOWED = check.allowed_from(ITEM["finding"], ITEM["facts"], "제21조 100 ha")


def _p(a1: str, a2: str = "현재 지휘권자는 의성군수입니다 [p.72, 시행령 제21조①].") -> list[dict]:
    return [
        {"action": "S1-a1", "category": "즉시", "text": a1},
        {"action": "S1-a2", "category": "대기", "text": a2},
    ]


def test_pass():
    errs = check.check_item(
        ITEM, _p("피해면적 48.5 ha로 1단계입니다. 발령을 협의하십시오 [p.73·91]."), ALLOWED, set()
    )
    assert errs == []


def test_number_not_in_input():
    errs = check.check_item(ITEM, _p("피해면적 50 ha입니다. 협의하십시오 [p.73]."), ALLOWED, set())
    assert any("50" in e for e in errs)


def test_time_and_korean_count():
    errs = check.check_item(ITEM, _p("15:30까지 세 곳을 확인하십시오 [p.73]."), ALLOWED, set())
    assert any("15:30" in e for e in errs) and any("세 곳" in e for e in errs)


def test_citation_outside_anchors_and_missing():
    errs = check.check_item(
        ITEM, _p("협의하십시오 [p.75].", "의성군수가 지휘합니다."), ALLOWED, set()
    )
    assert any("p.75" in e for e in errs)
    assert any("S1-a2" in e and "근거 표기" in e for e in errs)


def test_unknown_name_and_etc():
    errs = check.check_item(ITEM, _p("석탑리 등 주변을 지키십시오 [p.73]."), ALLOWED, {"석탑리"})
    assert any("석탑리" in e for e in errs) and any("등" in e for e in errs)


def test_parse_thinking_and_fence():
    body = '{"paragraphs": [{"action": "S1-a1", "category": "즉시", "text": "x"}]}'
    raw = "생각…</think>\n```json\n" + body + "\n```"
    assert check.parse(raw)[0]["action"] == "S1-a1"


def test_expand_citations():
    assert check.citations("가 [p.72·114, 시행령 제21조①]. 나 [규정 제8·9조].") == [
        "p.72",
        "p.114",
        "시행령 제21조①",
        "규정 제8조",
        "규정 제9조",
    ]


def test_assemble_adds_see_and_keeps_blocks():
    paras = {p["action"]: p for p in _p("협의하십시오 [p.73].")}
    out = assemble(ITEM, paras, "ok")
    blocks = out["sections"][0]["blocks"]
    assert [b["type"] for b in blocks] == ["paragraph", "list", "paragraph"]
    assert blocks[2]["text"].endswith("세부 내용은 S6 참조.")
    failed = assemble(ITEM, None, "failed")["sections"][0]["blocks"]
    assert failed[0]["type"] == "failed" and failed[1]["type"] == "list"


def test_tidy_and_placement_and_see():
    assert (
        check.tidy_citations("가 [p.73, p.91, 시행령 제21조①].") == "가 [p.73·91, 시행령 제21조①]."
    )
    errs = check.check_item(
        ITEM, _p("협의하십시오. [p.73]", "지휘합니다 [p.72]. 세부 내용은 S4 참조."), ALLOWED, set()
    )
    assert any("마침표 앞" in e for e in errs) and any("S4 참조" in e for e in errs)


def test_parse_bare_list():
    raw = '</think> [{"action": "S1-a1", "category": "즉시", "text": "x"}]'
    assert check.parse(raw)[0]["action"] == "S1-a1"
