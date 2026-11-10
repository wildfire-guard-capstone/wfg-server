# ruff: noqa: E501
"""상황 정정 해석 · 검사(assistant/correction.py, intent.py)."""

import json

import pytest

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


def test_helicopter_without_agency_asks_for_agency():
    """엔진은 기관 칸을 더해 합계를 다시 계산하므로 헬기는 기관이 있어야 반영된다."""
    out = preview(make_context(), "헬기 2대 추가")
    assert out["type"] == "UNSUPPORTED"
    assert "기관" in out["reason"]
    both = preview(make_context(), "괴산리 대피 완료, 헬기 2대 추가")
    assert both["type"] == "CORRECTION_PREVIEW" and len(both["changes"]) == 1
    assert "기관" in both["ignored"][0]["reason"]


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


def test_capacity_exceeded():
    ctx = make_context(capacity={"helicopter": 24})
    with pytest.raises(CorrectionError) as e:
        preview(ctx, "산림청 헬기 2대 추가")
    assert e.value.code == "RESOURCE_CAPACITY_EXCEEDED"


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
