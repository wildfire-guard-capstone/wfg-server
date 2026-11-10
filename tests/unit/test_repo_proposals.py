"""제안서 저장에서 계산하는 칸(DB 없이): 단계 · 구분 · 본문 · 진화/대피 · 바뀐 항목 수."""

import pytest
from rules_example import full_inputs

from agent.generate import assemble
from app.db.models.enums import ItemAxis, ItemCategory, ResponseStage
from app.repositories.proposals import AXIS_OF, STAGE_OF, categories_of, text_of
from rules.llm_input import build_llm_input

INCIDENT = {"name": "의성군 안평면 산불 (2025-03-22)", "sigungu": "의성군", "emd": "안평면"}


def para(category: str, body: str) -> dict:
    return {"type": "paragraph", "category": category, "body": body, "text": f"{body} [p.1]"}


SECTIONS = [
    {"title": "가", "blocks": [para("요청", "가를 요청하십시오."), {"type": "list", "rows": []}]},
    {"title": "나", "blocks": [para("즉시", "나를 하십시오."), para("요청", "다를 요청하십시오.")]},
]


def test_engine_stage_labels_map_to_db_stages():
    assert STAGE_OF == {
        "초기 대응": ResponseStage.INITIAL,
        "1단계": ResponseStage.STAGE1,
        "2단계": ResponseStage.STAGE2,
    }


def test_axis_from_item_code():
    assert AXIS_OF == {"S": ItemAxis.SUPPRESSION, "E": ItemAxis.EVACUATION}


def test_categories_once_in_fixed_order():
    assert categories_of(SECTIONS) == [ItemCategory.IMMEDIATE, ItemCategory.REQUEST]


def test_text_joins_bodies_without_citation_marks():
    assert text_of(SECTIONS) == "가를 요청하십시오.\n나를 하십시오.\n다를 요청하십시오."


def test_failed_item_has_no_categories_and_empty_text():
    engine = build_llm_input(full_inputs(), INCIDENT)
    e5 = next(it for it in engine["items"] if it["id"] == "E5")
    failed = assemble(e5, [], {}, "failed")  # 통과한 제안이 없을 때 에이전트 모양
    assert failed["sections"][0]["blocks"][0]["type"] == "failed"
    assert categories_of(failed["sections"]) == []
    assert text_of(failed["sections"]) == ""


def test_unknown_category_is_an_error():
    with pytest.raises(ValueError):
        categories_of([{"blocks": [para("기타", "…")]}])
