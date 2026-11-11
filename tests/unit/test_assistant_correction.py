# ruff: noqa: E501
"""상황 정정 해석 · 검사(assistant/correction.py, intent.py)."""

import json

import pytest

from app.services.assistant import apply_changes
from assistant import intent
from assistant.correction import (
    CorrectionError,
    counts_in,
    parse_rules,
    preview,
    read_count,
    resolve,
)
from tests.unit.assistant_example import make_context


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("괴산리 대피 언제까지 해야 해?", "QUESTION"),
        ("헬기 몇 대 투입됐어", "QUESTION"),
        ("2단계 기준이 뭐야", "QUESTION"),
        ("괴산리 대피 완료", "CORRECTION"),
        ("괴산리 주민 다 빠져나갔어", "CORRECTION"),
        ("헬기 두 대 더 왔어", "CORRECTION"),
        ("재난문자 보냈어", "CORRECTION"),
        ("확산대응 2단계 발령됐어", "CORRECTION"),
        ("안녕", "OTHER"),
        ("괴산리 대피 완료됐어?", None),  # 둘 다 → LLM(없으면 물음표라 질문)
    ],
)
def test_intent_rules(text, want):
    assert intent.by_rules(text) == want


def test_intent_falls_back_without_llm():
    assert intent.classify("괴산리 대피 완료됐어?") == "QUESTION"
    assert intent.classify("괴산리 대피 완료했는데 다음은 어디야") == "QUESTION"


def test_korean_counts():
    assert read_count("두") == 2
    assert read_count("스물두") == 22
    assert read_count("열") == 10
    assert read_count("1,200") == 1200
    assert read_count("") is None
    assert {2, 5} <= counts_in("헬기 두 대, 소방차 5대")


def test_parse_rules_villages_and_resources():
    p = parse_rules("괴산리, 신월리 대피 완료하고 산림청 헬기 2대 추가 투입")
    kinds = [(d.kind, d.village, d.count, d.group, d.mode) for d in p.drafts]
    assert ("helicopter", "", 2, "forest", "add") in kinds
    assert ("evacuated", "괴산리", None, "", "add") in kinds
    assert ("evacuated", "신월리", None, "", "add") in kinds


def test_preview_village_and_agency_helicopter():
    ctx = make_context()
    out = preview(ctx, "괴산리 대피 완료, 산림청 헬기 2대 추가")
    assert out["type"] == "CORRECTION_PREVIEW"
    by_item = {(c["target"], c["item"]): c for c in out["changes"]}
    v = by_item[("village", "evacuated")]
    assert (v["target_id"], v["before"], v["after"]) == ("101", False, True)
    assert by_item[("deployed", "helicopter.forest")]["after"] == 7
    t = by_item[("deployed", "helicopter.total")]
    assert (t["before"], t["after"]) == (23, 25)
    assert "괴산리" in out["summary"]
    assert ctx.situation["deployed"]["helicopter"]["total"] == 23  # 확인 전 저장 안 함


def test_helicopter_without_agency_goes_to_unknown_and_can_be_moved_later():
    """11-10 결정: 기관을 모르는 헬기는 「미상」 칸에 넣고, 나중에 기관 칸으로 옮겨 정정한다."""
    ctx = make_context()  # 헬기 23 = 산림청 5 · 지자체 16 · 소방 2
    out = preview(ctx, "헬기 2대 추가")
    items = {c["item"]: (c["before"], c["after"]) for c in out["changes"]}
    assert items == {"helicopter.unknown": (0, 2), "helicopter.total": (23, 25)}
    assert "미상" in out["notes"][0] and "미상 헬기 2대는 산림청" in out["notes"][0]
    # 확인하면 → 나중에 「미상 헬기 2대는 산림청이었어」: 미상에서 빼 산림청에 넣고, 합계 줄은 없음
    ctx.situation = apply_changes(ctx.situation, out["changes"], "13:00")
    moved = preview(ctx, "미상 헬기 2대는 산림청이었어")
    items = {c["item"]: (c["before"], c["after"]) for c in moved["changes"]}
    assert items == {"helicopter.unknown": (2, 0), "helicopter.forest": (5, 7)}
    # 합계만 「총 30대」 → 늘어난 7대는 미상
    total = preview(make_context(), "헬기 총 30대 투입 중")
    items = {c["item"]: (c["before"], c["after"]) for c in total["changes"]}
    assert items == {"helicopter.unknown": (0, 7), "helicopter.total": (23, 30)}


def test_move_uses_rules_even_if_llm_reads_only_the_add():
    """LLM이 「미상 헬기 2대는 산림청」을 「산림청 2대 추가」로만 읽어도 합계가 늘지 않게 규칙 해석을 쓴다."""
    from assistant.correction import Draft, Parsed, merge

    text = "미상 헬기 2대는 산림청이었어"
    llm = Parsed(source="llm", drafts=[Draft("helicopter", count=2, mode="add", group="forest")])
    got = merge(llm, parse_rules(text), text)
    assert [(d.mode, d.group) for d in got.drafts] == [("remove", "unknown"), ("add", "forest")]


def test_removing_more_than_unknown_asks_which_agency():
    """미상 칸에 뺄 만큼이 없으면 어느 기관이 줄었는지 묻는다(엉뚱한 칸을 줄이지 않게)."""
    out = preview(make_context(), "헬기 2대 철수")
    assert out["type"] == "UNSUPPORTED" and "어느 기관" in out["reason"]
    both = preview(make_context(), "괴산리 대피 완료, 헬기 2대 철수")
    assert both["type"] == "CORRECTION_PREVIEW" and len(both["changes"]) == 1
    assert "산림청 헬기 2대 철수" in both["ignored"][0]["reason"]


def test_total_only_report_counts_as_unknown_everywhere():
    """합계만 있는 보고({total: 25, 산림청 20})의 남는 5대는 정정 · 확인 · 엔진 입력 모두 미상 몫."""
    from app.repositories.engine_inputs import deployed_of
    from app.services.assistant import _current

    ctx = make_context()
    ctx.situation["deployed"]["helicopter"] = {"total": 25, "forest": 20}
    out = preview(ctx, "헬기 1대 철수")
    items = {c["item"]: (c["before"], c["after"]) for c in out["changes"]}
    assert items == {"helicopter.unknown": (5, 4), "helicopter.total": (25, 24)}
    assert all(_current(ctx.situation, c) == c["before"] for c in out["changes"])
    assert dict(deployed_of(ctx.situation).helicopter) == {"산림청": 20, "미상": 5}
    from assistant.tools import ToolBox  # 질문 답에도 미상 몫이 보인다

    heli = ToolBox(ctx).run("resources", "").facts[0]
    assert heli.value == "25대(산림청 20 · 미상 5)"


def test_crew_without_agency_goes_to_other_with_note():
    out = preview(make_context(), "인력 50명 추가 투입")
    items = {c["item"]: c for c in out["changes"]}
    assert items["crew.other"]["after"] == 50 and items["crew.total"]["after"] == 201
    assert out["notes"] and "기타" in out["notes"][0]


def test_set_and_remove_with_korean_numbers():
    out = preview(make_context(), "소방차 다섯 대 더 왔고 진화대 30명 철수")
    items = {c["item"]: (c["before"], c["after"]) for c in out["changes"]}
    assert items["vehicle.fire_truck"] == (15, 20)
    assert items["vehicle.total"] == (26, 31)
    assert items["crew.special_crew"] == (81, 51)
    assert items["crew.total"] == (151, 121)
    out = preview(make_context(), "지금 산림청 헬기 총 9대")
    items = {c["item"]: (c["before"], c["after"]) for c in out["changes"]}
    assert items["helicopter.forest"] == (5, 9) and items["helicopter.total"] == (23, 27)


def test_negative_count_rejected():
    with pytest.raises(CorrectionError) as e:
        preview(make_context(), "경찰 헬기 3대 철수")
    assert e.value.code == "INVALID_COUNT"


def test_capacity_from_resource_table_per_agency_slot():
    """11-10 결정: 상한은 자원 테이블 기준(칸별 대기 + 이 산불 투입). 넘는 줄만 빼고 나머지는 보인다."""
    ctx = make_context(capacity={"helicopter.forest": 6})  # 산림청 지금 5대
    ok = preview(ctx, "산림청 헬기 1대 추가")
    assert {c["item"] for c in ok["changes"]} == {"helicopter.forest", "helicopter.total"}
    over = preview(ctx, "괴산리 대피 완료, 산림청 헬기 2대 추가")
    assert [c["item"] for c in over["changes"]] == ["evacuated"]
    assert "가용 대수(6대" in over["ignored"][0]["reason"]
    only = preview(ctx, "산림청 헬기 2대 추가")
    assert only["type"] == "UNSUPPORTED" and "자원 현황" in only["reason"]
    # 자원 테이블에 없는 칸(군 · 미상)과 줄이는 정정은 상한을 보지 않는다
    assert preview(ctx, "군 헬기 9대 추가")["type"] == "CORRECTION_PREVIEW"
    assert preview(ctx, "헬기 9대 추가")["type"] == "CORRECTION_PREVIEW"
    tight = make_context(capacity={"helicopter.forest": 1})
    assert preview(tight, "산림청 헬기 2대 철수")["type"] == "CORRECTION_PREVIEW"


def test_capacity_rechecked_at_confirm():
    """확인안 뒤 자원 현황이 바뀌면 저장(COR-01)에서 409 RESOURCE_CAPACITY_EXCEEDED."""
    from assistant.correction import over_capacity

    assert over_capacity("helicopter.forest", 5, 7, {"helicopter.forest": 6})
    assert over_capacity("helicopter.forest", 5, 6, {"helicopter.forest": 6}) is None
    assert over_capacity("helicopter.military", 0, 9, {"helicopter.forest": 6}) is None


def test_targets_not_found_and_ambiguous():
    with pytest.raises(CorrectionError) as e:
        preview(make_context(), "없는리 대피 완료")
    assert e.value.code == "TARGET_NOT_FOUND"
    with pytest.raises(CorrectionError) as e:
        preview(make_context(), "하화리 대피 완료")
    assert e.value.code == "AMBIGUOUS_TARGET" and "옥산면" in e.value.message
    out = preview(make_context(), "옥산면 하화리 대피 완료")
    assert out["changes"][0]["target_id"] == "106"


def test_resolve_prefers_proposal_villages_then_home():
    ctx = make_context()
    assert [v["id"] for v in resolve(ctx, "괴산리")] == [101]  # 안동시 괴산리보다 제안서 마을
    assert [v["id"] for v in resolve(ctx, "괴산")] == [101]
    assert [v["id"] for v in resolve(ctx, "도옥리")] == [107]


def test_not_allowed_and_already_done():
    out = preview(make_context(), "확산대응 2단계 발령됐어")
    assert out["type"] == "UNSUPPORTED" and "대응단계" in out["reason"]
    out = preview(make_context(), "바람이 세졌어")
    assert out["type"] == "UNSUPPORTED" and "기상" in out["reason"]
    ctx = make_context()
    ctx.situation["villages"]["101"] = {"evacuated": True}
    out = preview(ctx, "괴산리 대피 완료")
    assert out["type"] == "UNSUPPORTED" and "이미" in out["reason"]


def test_evac_order_pre_evac_and_sms():
    out = preview(make_context(), "신월리 대피명령 내렸고 박곡리 사전대피 완료, 재난문자 보냈어")
    got = {(c["target"], c.get("target_id"), c["item"]) for c in out["changes"]}
    assert ("village", "102", "evac_order") in got
    assert ("village", "104", "pre_evac_done") in got
    assert ("situation", None, "disaster_sms_sent") in got


def test_ended_incident_rejects_correction():
    ctx = make_context()
    ctx.incident["status"] = "종료"
    with pytest.raises(CorrectionError) as e:
        preview(ctx, "괴산리 대피 완료")
    assert (e.value.status, e.value.code) == (409, "INCIDENT_ENDED")


class FakeLLM:
    def __init__(self, *answers):
        self.answers, self.seen, self.calls = list(answers), [], []

    def chat(self, messages, schema=None, **kw):
        self.seen.append((messages, schema, kw.get("tag")))
        self.calls.append({"tag": kw.get("tag")})
        a = self.answers.pop(0)
        return a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)


def change(kind, **kw):
    base = {
        "kind": kind,
        "village": "",
        "emd": "",
        "value": True,
        "count": 0,
        "mode": "add",
        "group": "",
        "text": "",
    }
    return base | kw


def test_llm_parse_used_and_invented_counts_dropped():
    llm = FakeLLM(
        {
            "changes": [
                change("evacuated", village="신월리", text="신월리 주민 피신"),
                change("helicopter", count=4, group="military", text="군 헬기"),  # 입력에 4가 없음
                change("crew", count=2, group="military", text="군 장병 두 명"),
            ],
            "not_allowed": [{"kind": "injury", "text": "부상자 1명"}],
        }
    )
    trace = {}
    out = preview(make_context(), "신월리 주민 피신 끝, 군 장병 두 명 도착, 부상자 1명", llm, trace)
    assert trace["correction_source"] == "llm+rules"
    items = {c["item"] for c in out["changes"]}
    assert items == {"evacuated", "crew.military", "crew.total"}
    reasons = " ".join(i["reason"] for i in out["ignored"])
    assert "수를 읽지 못했습니다" in reasons and "부상" in reasons
    schema = llm.seen[0][1]
    assert "신월리" in schema["properties"]["changes"]["items"]["properties"]["village"]["enum"]


def test_llm_choices_checked_against_input():
    """11-09 EXAONE 평가: 「군 헬기」 → 지자체, 「박곡리」(목록에 없음) → enum의 「도옥리」, 인력 빠뜨림,
    「2단계 발령」 → 그 밖. 입력 글과 규칙 해석으로 바로잡는다."""
    llm = FakeLLM(
        {
            "changes": [
                change("helicopter", count=3, group="local", text="군 헬기 3대 도착"),
                change("pre_evac_done", village="도옥리", text="박곡리 사전대피 끝"),
            ],
            "not_allowed": [{"kind": "other", "text": "2단계 발령됐어"}],
        }
    )
    trace = {}
    out = preview(
        make_context(),
        "군 헬기 3대 도착했고 인력 50명 추가, 박곡리 사전대피 끝, 2단계 발령됐어",
        llm,
        trace,
    )
    items = {c["item"]: (c.get("target_id"), c["before"], c["after"]) for c in out["changes"]}
    assert items["helicopter.military"] == (None, 0, 3)  # 지자체가 아니라 군
    assert items["crew.other"] == (None, 0, 50)  # LLM이 빠뜨린 인력은 규칙에서
    assert items["pre_evac_done"][0] == "104"  # 도옥리(107)가 아니라 박곡리(104)
    assert "pre_evac_done:도옥리" in trace["correction_llm_dropped"]
    assert any("대응단계" in i["reason"] for i in out["ignored"])


def test_llm_broken_json_falls_back_to_rules():
    trace = {}
    out = preview(make_context(), "괴산리 대피 완료", FakeLLM("not json"), trace)
    assert trace["correction_source"] == "rules" and out["type"] == "CORRECTION_PREVIEW"


# ── 11-11 실제 EXAONE 실험에서 나온 문제 3가지 ─────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "진화대원 2명 부상당했어",
        "소방관 한 명이 다쳤습니다",
        "공무원 3명 연락 두절",
        "진화대 5명 고립됐어",
    ],
)
def test_casualty_counts_are_not_deployment(text):
    """인명 피해 구절의 수는 투입 수가 아니다 — 규칙만으로도, LLM이 그 수를 투입으로 내도."""
    assert not [d for d in parse_rules(text).drafts if d.kind == "crew"]
    out = preview(make_context(), text)
    assert out["type"] == "UNSUPPORTED" and "부상" in out["reason"]
    n = next(iter(counts_in(text)))
    llm = FakeLLM({"changes": [change("crew", count=n, group="special_crew")], "not_allowed": []})
    assert preview(make_context(), text, llm)["type"] == "UNSUPPORTED"


def test_casualty_does_not_block_other_changes_in_the_sentence():
    out = preview(make_context(), "진화대원 2명 부상당했고, 소방차 3대 추가 도착")
    items = {c["item"] for c in out["changes"]}
    assert items == {"vehicle.fire_truck", "vehicle.total"}
    assert any("부상" in i["reason"] for i in out["ignored"])


def test_repeated_and_unsupported_llm_changes_are_dropped():
    """EXAONE이 같은 변화를 되풀이하고 입력에 없는 대피 · 재난문자 변화까지 낸 경우(8번 중 2번)."""
    heli = change("helicopter", count=1, group="forest", mode="add", text="산림청 헬기 1대 추가")
    llm = FakeLLM(
        {
            "changes": [
                heli,
                heli,
                heli,
                change("evacuated", value=False, mode="set"),
                change("evac_order", value=False, mode="set"),
                change("disaster_sms_sent", value=False, mode="set"),
                change("disaster_sms_sent", value=False, mode="set"),
            ],
            "not_allowed": [
                {"kind": "weather", "text": "바람"},
                {"kind": "weather", "text": "바람"},
            ],
        }
    )
    ctx = make_context()
    before = ctx.situation["deployed"]["helicopter"]["forest"]
    trace = {}
    out = preview(ctx, "산림청 헬기 1대 추가했고 바람이 많이 세졌어", llm, trace)
    items = {c["item"]: (c["before"], c["after"]) for c in out["changes"]}
    assert set(items) == {"helicopter.forest", "helicopter.total"}  # 재난문자 변화 없음
    assert items["helicopter.forest"] == (before, before + 1)  # 세 번이 아니라 한 번
    assert [i["reason"] for i in out["ignored"]].count(out["ignored"][0]["reason"]) == 1
    assert "기상" in out["ignored"][0]["reason"] and len(out["ignored"]) == 1
    assert "repeat:helicopter" in trace["correction_llm_dropped"]


def test_same_phrase_twice_in_the_input_is_counted_twice():
    one = change("helicopter", count=1, group="forest", mode="add")
    ctx = make_context()
    before = ctx.situation["deployed"]["helicopter"]["forest"]
    text = "산림청 헬기 1대 도착했고, 조금 뒤 산림청 헬기 1대 더 왔어"
    out = preview(ctx, text, FakeLLM({"changes": [one, one], "not_allowed": []}))
    items = {c["item"]: (c["before"], c["after"]) for c in out["changes"]}
    assert items["helicopter.forest"] == (before, before + 2)


@pytest.mark.parametrize(
    "text",
    [
        "재난문자는 아직 안 나갔어. 아까 잘못 입력했어",
        "재난문자 안 보냈어",
        "문자 발송 취소됐습니다",
        "재난문자 송출은 아직 못 했어",
    ],
)
def test_sms_can_be_taken_back(text):
    """재난문자 송출 되돌리기: 규칙이 읽으므로 LLM이 못 읽거나(8번 중 5번) 거꾸로 읽어도 된다."""
    assert intent.by_rules(text) == "CORRECTION"
    for llm in (
        None,
        FakeLLM({"changes": [], "not_allowed": [{"kind": "other", "text": text}]}),
        FakeLLM({"changes": [change("disaster_sms_sent", value=True)], "not_allowed": []}),
    ):
        ctx = make_context()
        ctx.situation["disaster_sms_sent"] = True
        out = preview(ctx, text, llm)
        assert out["type"] == "CORRECTION_PREVIEW", out
        assert [(c["item"], c["before"], c["after"]) for c in out["changes"]] == [
            ("disaster_sms_sent", True, False)
        ]


def test_sms_sent_still_reads_as_sent():
    ctx = make_context()
    ctx.situation["disaster_sms_sent"] = False
    out = preview(ctx, "재난문자 보냈어")
    assert [(c["item"], c["after"]) for c in out["changes"]] == [("disaster_sms_sent", True)]


def test_false_value_needs_a_negation_in_the_input():
    """「아니오」로 되돌리는 변화는 입력에 취소 · 잘못 · 아직 같은 말이 있을 때만 받는다."""
    ctx = make_context()
    ctx.situation.setdefault("villages", {})["101"] = {"evacuated": True}
    llm = FakeLLM(
        {"changes": [change("evacuated", village="괴산리", value=False)], "not_allowed": []}
    )
    out = preview(ctx, "괴산리 대피 완료는 잘못 보고된 거야. 아직 대피 중이야", llm)
    assert [(c["item"], c["before"], c["after"]) for c in out["changes"]] == [
        ("evacuated", True, False)
    ]
    trace = {}
    llm = FakeLLM(
        {"changes": [change("evac_order", village="신월리", value=False)], "not_allowed": []}
    )
    out = preview(make_context(), "신월리 주민들이 이동하고 있습니다", llm, trace)
    assert out["type"] == "UNSUPPORTED" and "evac_order:false" in trace["correction_llm_dropped"]
