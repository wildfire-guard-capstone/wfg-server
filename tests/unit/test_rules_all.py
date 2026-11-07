"""전체 흐름: 엔진 입력 하나 → 묶음 → 12개 항목 입력 + 섹션(S6 · E6 요청 표 포함)."""

import json

from rules_example import full_inputs

from rules.llm_input import build_llm_input

INCIDENT = {"name": "의성군 안평면 산불 (2025-03-22)", "sigungu": "의성군", "emd": "안평면"}


def test_whole_proposal_has_12_items_and_request_tables():
    out = build_llm_input(full_inputs(), INCIDENT)
    items = {it["id"]: it for it in out["items"]}
    assert list(items) == ["S1", "S2", "S3", "S4", "S5", "S6", "E1", "E2", "E3", "E4", "E5", "E6"]
    assert out["common"]["current_stage"] == "확산대응 1단계"
    assert out["common"]["commander"] == {"now": "의성군수", "next": "경상북도지사"}

    def groups(item):
        (table,) = [b for s in item["sections"] for b in s["blocks"] if b["type"] == "table"]
        return [r["group"] for r in table["rows"]]

    assert groups(items["S6"]) == [
        "소방",
        "전력",
        "경찰",
        "국가유산",
        "산림 헬기",
        "임차 헬기",
    ]
    assert groups(items["E6"]) == ["보건복지", "의료", "교통", "치안", "재난방송", "마을 조직"]
    (table,) = [b for s in items["E6"]["sections"] for b in s["blocks"] if b["type"] == "table"]
    broadcast = next(r for r in table["rows"] if r["group"] == "재난방송")
    assert broadcast["agency"] == "중앙재난안전상황실 또는 경상북도 재난안전상황실"
    # E6은 LLM을 부르지 않는다(action 블록 없음)
    assert not [b for s in items["E6"]["sections"] for b in s["blocks"] if b["type"] == "action"]
    # 엔진 JSON · LLM 입력은 그대로 JSON으로 저장할 수 있다
    json.dumps(out, ensure_ascii=False)
