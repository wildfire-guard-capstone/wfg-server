"""제안 검사(11/8 재설계): 근거 · 판정 값 · 권한 · 형식."""

from agent import check

JUDGED = "의성군수 확산대응 1단계 피해면적 48.5 ha 신월리 주택 최소 2동 14:50 경상북도지사"
EV = {"SM-p079-s02", "SM-p091-s01"}
EV_TEXT = "보호 대상 건물 둘레에 소방차와 진화차를 모아 두고 필요하면 헬기로 물을 뿌림 ... 8.2km"
ALLOWED = check.allowed_from(JUDGED, EV_TEXT)
NAMES = {"신월리", "석탑리"}


def _c(text, ev=("SM-p079-s02",), cat="즉시", reader="의성군수", item="S2"):
    p = {"category": cat, "text": text, "evidence": list(ev)}
    return check.check_one(item, p, EV, ALLOWED, JUDGED, NAMES, reader)


def test_pass():
    assert _c("신월리 주택 최소 2동 주변에 소방차와 진화차를 집중 배치하십시오.") == []


def test_needs_evidence_from_given_chunks():
    assert any("evidence" in e for e in _c("소방차를 배치하십시오.", ev=()))
    assert any("evidence" in e for e in _c("소방차를 배치하십시오.", ev=("SM-p999-s01",)))


def test_numbers_times_names_stages():
    errs = _c("석탑리에 15:30까지 50 ha 기준 확산대응 2단계로 세 곳을 배치하십시오.")
    joined = " ".join(errs)
    assert "15:30" in joined and "50" in joined and "석탑리" in joined
    assert "확산대응 2단계" in joined and "세 곳" in joined


def test_form_rules():
    joined = " ".join(_c("신월리 주민 등을 대피하지 않습니다. 판정 자료를 보면 됩니다."))
    assert "등" in joined and "부정" in joined and "하십시오" in joined and "입력 설명" in joined


def test_no_authority_check_any_more():
    """11/19 재설계(P1): 권한 검사는 다시 쓰기 지시로 법 지식을 넣으므로 없앴다."""
    assert _c("신월리에 대피명령을 내리십시오.", reader="경상북도지사", item="E2") == []
    ok = _c(
        "의성군수에게 신월리 대피명령을 내리도록 지시하십시오.", reader="경상북도지사", item="E2"
    )
    assert ok == []


def test_min_houses_and_forbid():
    assert any("최소 2동" in e for e in _c("신월리 주택 2동을 보호하십시오."))
    assert _c("헬기 2대를 추가로 투입하십시오.", item="S3")  # 추가 대수 금지 · 숫자 2는 판정에 있음


def test_duplicates_and_parse_tidy():
    props = [
        {"category": "즉시", "text": "소방차를 배치하십시오.", "evidence": ["SM-p079-s02"]},
        {"category": "즉시", "text": "소방차를 배치하십시오.", "evidence": ["SM-p079-s02"]},
    ]
    bad = check.check("S2", props, EV, ALLOWED, JUDGED, NAMES)
    assert list(bad) == [1]
    raw = '</think>```json\n{"proposals": [{"category": "즉시", "text": "x", "evidence": []}]}\n```'
    assert check.parse(raw)[0]["text"] == "x"
    assert check.tidy("배치하십시오 [p.79].") == "배치하십시오."
    assert check.fix_category("산림청장과 협의하십시오.", "즉시") == "협의"
    assert check.fix_category("배치하십시오.", "요청") == "즉시"


def test_subject_strip_and_vague():
    assert (
        check.strip_subject("의성군수는 2단계 발령을 협의하십시오.", "의성군수")
        == "2단계 발령을 협의하십시오."
    )
    assert (
        check.strip_subject("상황입니다. 의성군수는 협의하십시오.", "의성군수")
        == "상황입니다. 협의하십시오."
    )
    assert any("방법이 빠진" in e for e in _c("신월리 주택 최소 2동에 보호 조치를 취하십시오."))


def test_quote_must_be_in_cited_text():
    texts = {"SM-p079-s02": EV_TEXT}
    p = {"category": "즉시", "text": "소방차를 집중 배치하십시오.", "evidence": ["SM-p079-s02"]}
    ok = check.check_one(
        "S2",
        p | {"quote": "소방차와 진화차를 모아 두고"},
        EV,
        ALLOWED,
        JUDGED,
        NAMES,
        "의성군수",
        texts,
    )
    bad = check.check_one(
        "S2",
        p | {"quote": "헬기 3대를 띄운다"},
        EV,
        ALLOWED,
        JUDGED,
        NAMES,
        "의성군수",
        texts,
        True,
    )
    assert ok == [] and any("quote" in e for e in bad)


def test_quote_allows_changed_endings():
    src = "산불의 진화와 관련한 모든 사항을 시ㆍ도지사에게 인계해야 한다."
    assert check.quote_ok("산불의 진화와 관련한 모든 사항을 시ㆍ도지사에게 인계해야 합니다", [src])
    assert not check.quote_ok("헬기를 세 대 더 투입한다", [src])


def test_self_address_and_split_targets():
    assert any(
        "자신에게" in e
        for e in _c("의성군수에게 협의를 준비하도록 지시하십시오.", reader="의성군수")
    )
    props = [
        {"category": "즉시", "text": "신월리 주민을 대피시키십시오.", "evidence": ["SM-p079-s02"]},
        {"category": "즉시", "text": "석탑리 주민을 대피시키십시오.", "evidence": ["SM-p079-s02"]},
    ]
    bad = check.check("E2", props, EV, ALLOWED, JUDGED + " 석탑리", NAMES)
    assert 1 in bad and any("모아" in e for e in bad[1])


def test_stage_compared_by_number():
    assert _c("신월리 주택 최소 2동 주변에 확산대응 1단계에 맞춰 소방차를 배치하십시오.") == []
    assert any("단계" in e for e in _c("확산대응 3단계 발령을 준비하십시오."))


def test_placeholder_vocative_and_repeat():
    assert any(
        "가린 값" in e for e in _c("서풍이 N m/s로 강해지는 N~N시에 지상 진화로 바꾸십시오.")
    )
    assert (
        check.strip_subject("의성군수님, 소방차를 배치하십시오.", "의성군수")
        == "소방차를 배치하십시오."
    )
    assert (
        check.tidy("신월리 신월리 주택 주변에 배치하십시오") == "신월리 주택 주변에 배치하십시오."
    )


def test_time_suffix_and_unknown_village():
    assert check.tidy("서풍이 강해지는 15:10~16:10시에 지상 진화로 바꾸십시오") == (
        "서풍이 강해지는 15:10~16:10에 지상 진화로 바꾸십시오."
    )
    # 지도 · 판정 · 근거 어디에도 없는 「○○리」(신안리를 신신리로 잘못 씀)
    assert any(
        "마을 이름 「신신리」" in e for e in _c("신월리~신신리 구간 주변에 소방차를 배치하십시오.")
    )
    assert not any(
        "마을 이름" in e
        for e in _c("신월리 주택 최소 2동 주변에 소방차를 배치하고 일자리 는 그대로 두십시오.")
    )


# 행정리 이름(11/19 R3): 엔진이 닿는 행정리를 「신안리(신안1리)」로 넘긴다
ADMIN_JUDGED = (
    "위험구역 2곳: 안평면 신월리 · 신안리(신안1리) 주택 최소 11동 거리는 신안1리노인회관에서"
)
ADMIN_ALLOWED = check.allowed_from(ADMIN_JUDGED, EV_TEXT)


def _a(text, names=frozenset({"신월리", "신안리"})):
    p = {"category": "즉시", "text": text, "evidence": ["SM-p079-s02"]}
    return check.check_one("E2", p, EV, ADMIN_ALLOWED, ADMIN_JUDGED, set(names), "의성군수")


def test_admin_village_in_judgment_passes():
    assert _a("신안1리 주민을 대피시키십시오.") == []
    assert _a("신안리(신안1리) 주민 대피를 지원하십시오.") == []
    assert _a("신안1리노인회관에 모이도록 안내하십시오.") == []


def test_admin_village_not_in_judgment_is_caught():
    # 닿지 않는 신안2리 · 없는 신안4리: 숫자 검사가 아니라 마을 이름 검사로 잡는다
    errs = _a("신안2리와 신안4리 주민을 대피시키십시오.")
    assert any("마을 이름 「신안2리」" in e for e in errs)
    assert any("마을 이름 「신안4리」" in e for e in errs)
    assert not any("숫자" in e for e in errs)
    # 지도에 있는 행정리면 「판정 · 근거에 없는 이름」으로 잡는다(known_names)
    errs = _a("신안2리 주민을 대피시키십시오.", names={"신월리", "신안리", "신안2리"})
    assert any("이름 「신안2리」" in e for e in errs)


def test_village_before_admin_paren_is_checked():
    assert any("마을 이름 「신신리」" in e for e in _a("신신리(신안1리) 주민을 대피시키십시오."))


def test_admin_number_is_not_a_count():
    _, nums = check._nums("신안1리 · 용기6리 주민 12명, 물 2리터")
    assert nums == {"12", "2"}


def test_pair_quote_with_admin_village_is_not_a_message_text():
    text = (
        "「신안리(신안1리) 주민은 안평초등학교로, 신월리 주민은 의성여자고등학교로」 안내하십시오."
    )
    assert check.strip_pairs(text) == " 안내하십시오."
    assert check.strip_pairs("「용기6리 주민은 안계고등학교로」") == ""
