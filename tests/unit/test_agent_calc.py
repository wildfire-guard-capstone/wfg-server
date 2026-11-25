# ruff: noqa: E501
"""판단 계산 값(agent/calc.py, 11/17 네 단계): 대피소 수용 비교 · 마을–대피 장소 짝. 값은 의성 회차 1(12:50) 판정."""

from agent import calc


def _row(village, people, zone, hubs):
    return {
        "village": village,
        "people": people,
        "zone": zone,
        "until": "지금" if zone == "burning" else "14:50",
        "hub_sites": [{"name": n, "capacity": c, "km": 1.0} for n, c in hubs],
    }


E4 = {
    "id": "E4",
    "facts": {
        "shelters": [
            _row("괴산리", 147, "burning", [("안평초등학교", 109), ("봉양중학교", 605)]),
            _row("신월리", 128, "burning", [("안평초등학교", 109), ("의성여자고등학교", 316)]),
            _row("양곡리", 245, "p5", [("의성국민체육센터", 1422), ("안계노인복지관", 62)]),
            _row("용기리", 2623, "p5", [("의성군립도서관", 62), ("안계고등학교", 726)]),
            _row("신안리", 181, "p5", [("안평초등학교", 109), ("봉양중학교", 605)]),
            _row("석탑리", 177, "p5", [("안평초등학교", 109), ("의성여자고등학교", 316)]),
            _row("박곡리", 280, "p8", [("안평초등학교", 109), ("봉양중학교", 605)]),
            {
                "village": "빈리",
                "people": 0,
                "zone": "p5",
                "hub_sites": [],
            },  # 인원 없는 마을은 뺀다
        ]
    },
}


def test_shelter_check_matches_document_example():
    lines = calc.for_item("E4", {"E4": E4})
    text = "\n".join(lines)
    assert lines[0].startswith("(가정)")
    # 문서 9.10 E4 판단: 275명 → 166명 넘침, 다섯 마을 913명, 용기리 788명 → 1,835명 모자람, 양곡리 충분
    assert (
        "지금 대피할 괴산리 · 신월리 275명 → 첫 후보 안평초등학교(수용 109명)에 166명이 넘칩니다."
        in text
    )
    assert (
        "안평초등학교(수용 109명)를 첫 후보로 둔 마을 5곳(괴산리 · 신월리 · 신안리 · 석탑리 · 박곡리)을 합치면 913명이라 804명이 넘칩니다."
        in text
    )
    assert (
        "용기리 2,623명은 두 후보 의성군립도서관(62명) · 안계고등학교(726명)를 합쳐도 788명이라 1,835명이 모자랍니다."
        in text
    )
    assert "첫 후보 하나로 충분한 마을: 양곡리." in text
    assert "빈리" not in text


def test_pairing_lines_give_one_place_or_two_when_first_is_short():
    """11/25: E4가 정한 대피소를 E5도 그대로 쓴다(「대피 장소 미정」 모순, QA 4-8b)."""
    lines = calc.for_item("E5", {"E4": E4})
    assert "양곡리 → 의성국민체육센터" in lines
    assert "괴산리 → 안평초등학교 · 봉양중학교" in lines
    assert len(lines) == 8 and "정할 수 없" not in "".join(lines)


def test_e3_gets_no_transfer_agency_any_more():
    """11/19 재설계 D3: 「거주자 이송을 맡을 기관」은 담당 지정이라 규칙층(판단 계산 값)에서 뺐다."""
    e6 = {
        "id": "E6",
        "sections": [
            {"blocks": [{"type": "table", "rows": [{"group": "보건", "agency": "의성군보건소장"}]}]}
        ],
    }
    assert calc.for_item("E3", {"E3": {"id": "E3"}, "E6": e6}) == []


def test_no_e4_or_other_items_give_nothing():
    assert calc.for_item("E5", {}) == []
    assert calc.for_item("S1", {"E4": E4}) == []
    assert calc.for_item("E4", {"E4": {"id": "E4", "facts": {}}}) == []
