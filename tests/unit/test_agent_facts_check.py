# ruff: noqa: E501
"""상황 · 상태 · 판단 검사와 제안 정리(11/17 네 단계 — 회차 1 시험에서 나온 오류를 막는 것)."""

from agent import calc, check

BLOB = "의성군 안평면 산불 (2025-03-22) 괴산리 신월리 양곡리 의성국민체육센터 안계초등학교 48.5 14:50 100"
ALLOWED = check.allowed_from(BLOB)


def test_tidy_fact_strips_labels_and_korean_time():
    assert check.tidy_fact(": 현재 피해면적은 48.5 ha입니다") == "현재 피해면적은 48.5 ha입니다."
    assert check.tidy_fact("(판단) 산불이 커집니다.") == "산불이 커집니다."
    assert check.tidy_fact("situation: 바람이 셉니다.") == "바람이 셉니다."
    assert check.tidy_fact("14시 50분 무렵 100 ha를 넘습니다.") == "14:50 무렵 100 ha를 넘습니다."


def test_invented_fire_name_is_caught_in_facts_only():
    facts, errs, removed = check.check_facts(
        "S3",
        {
            "situation": "고장산 산불은 커지고 있습니다. 의성군 안평면 산불은 14:50 무렵 100 ha를 넘습니다.",
            "status": "",
            "judgment": "",
        },
        ALLOWED,
        BLOB,
        set(),
    )
    assert facts["situation"] == "의성군 안평면 산불은 14:50 무렵 100 ha를 넘습니다."
    assert "산불 이름" in removed[0]["errors"][0]
    _, _, removed2 = check.check_facts(
        "S3",
        {"situation": "고산불은 커지고 있습니다. 대형산불이 됩니다.", "status": ""},
        ALLOWED,
        BLOB,
        set(),
    )
    assert [r["text"] for r in removed2] == ["고산불은 커지고 있습니다."]


def test_village_shelter_pair_is_not_a_message_draft():
    p = {
        "category": "요청",
        "text": "문자에는 「양곡리 주민은 의성국민체육센터로, 도덕리 주민은 안계초등학교로」처럼 마을과 대피 장소를 짝지어 넣게 하십시오.",
        "evidence": ["X"],
    }
    errs = check.check_one(
        "E5", p, {"X"}, check.allowed_from(BLOB, "도덕리"), BLOB, set(), "의성군수"
    )
    assert not any("문안" in e for e in errs)
    draft = p | {"text": "「주민 여러분 지금 바로 대피하시기 바랍니다」라고 보내십시오."}
    assert any("문안" in e for e in check.check_one("E5", draft, {"X"}, ALLOWED, BLOB, set(), ""))


def test_drop_repeated_sentence_across_proposals():
    props = [
        {"text": "지금 범위 안의 진화자원을 확산에 맞춰 단계적으로 늘려 투입하십시오."},
        {
            "text": "지금 범위 안의 진화자원을 확산에 맞춰 단계적으로 늘려 투입하십시오. 2단계 범위의 응원 요청을 준비하십시오."
        },
        {"text": "지금 범위 안의 진화자원을 확산에 맞춰 단계적으로 늘려 투입하십시오."},
    ]
    removed, whole = check.drop_repeated(props)
    assert props[1]["text"] == "2단계 범위의 응원 요청을 준비하십시오."
    assert whole == {2} and removed[0]["i"] == 1
    bad = check.check("S3", props, set(), ALLOWED, BLOB, set(), repeated=whole)
    assert any("앞 제안과 같은 내용" in e for e in bad[2])


def test_zone_and_deployed_lines():
    e1 = {
        "facts": {
            "zones": {
                "burning": [{"name": "괴산리"}, {"name": "신월리"}],
                "p5": [{"name": "양곡리"}],
                "p8": [{"name": "박곡리"}],
            }
        }
    }
    s3 = {"facts": {"deployed": {"helicopter": {"total": 23}, "crew": {"total": 151}}}}
    by = {"E1": e1, "S3": s3}
    assert calc.for_item("E1", by) == [
        "위험구역(5시간 안, 이미 불이 닿은 마을 포함) 3개 리: 괴산리 · 신월리 · 양곡리 (이미 닿음 2개 리: 괴산리 · 신월리)",
        "잠재 위험구역(5 ~ 8시간) 1개 리: 박곡리",
    ]
    assert calc.for_item("S4", by) == ["지금 투입된 진화자원(S3 판정): 헬기 23대, 인력 151명"]


def test_now_area_paired_with_other_time_is_caught():
    errs = check.now_area_errors("산불은 14:50 무렵 48.5 ha로 확산 중입니다.", "12:50", 48.5)
    assert errs and "12:50" in errs[0]
    assert check.now_area_errors("12:50 현재 피해면적은 48.5 ha입니다.", "12:50", 48.5) == []
    assert check.now_area_errors("14:50 무렵 100 ha를 넘습니다.", "12:50", 48.5) == []


def test_language_and_zone_code_and_fire_name_false_positive():
    assert check.language_errors("Information on evacuation shelters is not available here.")
    assert check.language_errors("송전선 2 km가 p5 구역에 듭니다.")
    assert check.language_errors("송전선 2 km가 5시간 범위에 듭니다.") == []
    assert check.fire_name_errors("바람이 점점 세져 산불 확산이 빨라집니다.", BLOB) == []
    assert check.fire_name_errors("고장산 산불은 커집니다.", BLOB)


def test_noinfo_filler_dropped_but_withheld_kept():
    wh = ["대피소 실제 개방 여부와 남은 수용 인원(실시간 자료 없음)"]
    assert check.is_noinfo_filler("현재 자원 투입 현황과 병원 정보는 없습니다.", wh)
    assert not check.is_noinfo_filler(
        "대피소 실제 개방 여부와 남은 수용 인원은 정보가 없습니다.", wh
    )
    assert not check.is_noinfo_filler("주택은 신월리 최소 1동이고 대피소 정보는 없습니다.", wh)
    facts, errs, removed = check.check_facts(
        "E4",
        {"situation": "괴산리에는 이미 불이 닿았습니다. 투입 자원 정보는 없습니다.", "status": ""},
        check.allowed_from(BLOB),
        BLOB,
        set(),
        withheld=wh,
    )
    assert facts["situation"] == "괴산리에는 이미 불이 닿았습니다." and removed[0]["filler"]
    assert errs == []  # 군말은 다시 쓰게 하지 않는다


def test_now_area_not_flagged_when_now_word_between():
    assert (
        check.now_area_errors("11:24에 시작되어 현재 48.5 ha로 확산 중입니다.", "12:50", 48.5) == []
    )
