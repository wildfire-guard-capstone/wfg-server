# ruff: noqa: E501
"""마지막 글 다듬기(agent/polish.py, 11/25): 마을 이름 꼴 · 표기만 고친다."""

from agent.polish import fix_marks, fix_names, fix_reason, polish, village_labels

LABELS = {"구계리": ["구계2리"], "박곡리": ["박곡1리", "박곡3리"]}


def test_labels_come_from_e1_zones():
    zones = {"burning": [], "p5": [{"name": "박곡리(박곡1리, 박곡3리)"}, {"name": "석탑리"}]}
    assert village_labels({"E1": {"facts": {"zones": zones}}}) == {"박곡리": ["박곡1리", "박곡3리"]}
    assert village_labels({}) == {}


def test_first_mention_becomes_full_name():
    assert (
        fix_names("구계리는 이미 가깝고 구계리 이장", LABELS)
        == "구계리(구계2리)는 이미 가깝고 구계리 이장"
    )
    assert fix_names("구계2리 주민 116명", LABELS) == "구계리(구계2리) 주민 116명"
    # 행정리 하나만 말하면 그 행정리만 쓴다(인원이 다를 수 있음)
    assert fix_names("박곡1리 주민", LABELS) == "박곡리(박곡1리) 주민"
    both = "박곡리(박곡1리, 박곡3리)가 포함되므로"
    assert fix_names("박곡1리, 박곡3리가 포함되므로", LABELS) == both
    assert fix_names("박곡리 주민 180명", LABELS) == "박곡리(박곡1리, 박곡3리) 주민 180명"
    same = "박곡리(박곡1리, 박곡3리) 180명과 박곡리 이장"
    assert fix_names(same, LABELS) == same


def test_places_and_facilities_are_left_alone():
    for text in (
        "의성군 단촌면 구계리 산 12 일원",
        "괴산리~구계리 구간 송전선",
        "구계2리경로당으로 안내",
        "고운사삼층석탑(단촌면 구계리)에 배치",
        "고운사요양원(노인요양원, 구계리) 거주자",
        "구계1리 주민",  # 대피 대상이 아닌 행정리
    ):
        assert fix_names(text, LABELS) == text


def test_marks():
    assert fix_marks("배치 ( )를 요청(S6)과 함께") == "배치를 요청과 함께"
    assert fix_marks("S6(유관기관 동원)과 함께") == "유관기관 동원과 함께"
    assert fix_marks("국가유산청에 (S6)와 함께 요청") == "국가유산청에 요청"
    assert fix_marks("진화 지원을(S6)과 함께 요청") == "진화 지원을 요청"
    assert fix_marks("S6 유관기관 동원·협조 요청과 함께") == "유관기관 동원·협조 요청과 함께"
    assert fix_marks("S6 및 E6 요청") == "S6 및 E6 요청"
    assert fix_marks("대피명령이 없음므로") == "대피명령이 없으므로"
    assert fix_marks("인력이 부족므로") == "인력이 부족하므로"
    assert fix_marks("협조를 요청하십시오..") == "협조를 요청하십시오."
    assert fix_marks("배치되지 않음므로") == "배치되지 않으므로"
    assert fix_marks("있으므로") == "있으므로"
    assert fix_marks("가까워질 수 있다므로") == "가까워질 수 있으므로"
    assert fix_reason("투입 수 정보 없음") == "투입 수 정보 없으므로"
    assert fix_reason("234명이 넘친다.") == "234명이 넘치므로"
    assert fix_reason("수용이 부족하므로") == "수용이 부족하므로"


def test_polish_changes_text_but_not_quote():
    final = {
        "": {
            "reason": "구계리가 가까우므로",
            "proposals": [{"text": "구계리 주민을 대피시키십시오."}],
            "basis": [{"text": "판정 값: 구계리 100명", "quote": "구계리"}],
        }
    }
    facts = {"situation": "구계리에 접근 중입니다."}
    polish("E2", final, facts, LABELS)
    p = final[""]
    assert p["reason"] == "구계리(구계2리)가 가까우므로"
    assert p["proposals"][0]["text"] == "구계리(구계2리) 주민을 대피시키십시오."
    assert p["basis"][0] == {"text": "판정 값: 구계리(구계2리) 100명", "quote": "구계리"}
    assert facts["situation"].startswith("구계리(구계2리)에")
    facts = {"situation": "구계리 산불"}
    polish("S1", {}, facts, LABELS)  # 마을을 다루지 않는 항목은 이름을 건드리지 않는다
    assert facts["situation"] == "구계리 산불"
