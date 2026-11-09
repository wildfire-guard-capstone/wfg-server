"""「변경」 판단(DB 없이): CHANGED · WORDING_ONLY · SAME · NEW · REMOVED와 바뀐 부분 고르기."""

from dataclasses import dataclass, field, replace
from typing import Any

from app.db.models.enums import ItemCategory
from app.services.proposal_compare import change_of, changed_count, changes, item_diff, rows_of


@dataclass(frozen=True)
class Item:
    item_code: str
    categories: list[Any]
    sections: list[dict[str, Any]]
    detail: dict[str, Any] = field(default_factory=dict)


def para(topic: str | None, situation: str, action: str, category: str = "즉시") -> dict:
    return {
        "type": "paragraph",
        "category": category,
        "topic": topic,
        "situation": situation,
        "action_text": action,
        "body": f"{situation} {action}".strip(),
    }


def item(code="S3", finding="투입: 헬기 23", paras=None, rows=("지금: **박곡리**(28명)",)) -> Item:
    paras = paras or [
        para("투입 자원", "현재 헬기 23대가 투입되어 있습니다.", "집중 투입하십시오.")
    ]
    blocks = [*paras, {"type": "list", "rows": [{"text": r} for r in rows]}]
    return Item(
        code,
        [ItemCategory.IMMEDIATE],
        [{"title": "1. 투입 현황", "blocks": blocks}],
        {"name": "진화자원 현황·동원 범위", "finding": finding},
    )


def test_same_and_new_and_removed():
    a = item()
    assert change_of(a, item()) == "SAME"
    assert change_of(None, a) == "NEW"
    assert change_of(a, None) == "REMOVED"
    assert change_of(None, None) is None


def test_only_llm_wording_is_not_a_change():
    a = item()
    b = item(
        paras=[
            para(
                "투입 자원", "현재 헬기 23대가 투입되어 있습니다.", "가용 헬기를 모두 투입하십시오."
            )
        ]
    )
    assert change_of(a, b) == "WORDING_ONLY"
    assert changed_count([a], [b]) == 0


def test_engine_side_changes():
    a = item()
    assert change_of(a, item(finding="투입: 헬기 29")) == "CHANGED"
    assert change_of(a, item(rows=("지금: **박곡리**(대피 완료)",))) == "CHANGED"
    assert change_of(a, replace(a, categories=[ItemCategory.STANDBY])) == "CHANGED"
    moved = item(
        paras=[para("투입 자원", "현재 헬기 29대가 투입되어 있습니다.", "집중 투입하십시오.")]
    )
    assert change_of(a, moved) == "CHANGED"


def test_relative_wording_is_ignored():
    a = item(code="S1", finding="현재 1단계(피해면적 120 ha, 직전 초기 대응) → 5시간 뒤 2단계")
    b = item(code="S1", finding="현재 1단계(피해면적 120 ha, 직전 1단계) → 5시간 뒤 2단계")
    assert change_of(a, b) == "SAME"


def test_changes_skip_codes_absent_in_both():
    s3, e2 = item(), item(code="E2")
    assert changes([s3], [s3, e2]) == {"S3": "SAME", "E2": "NEW"}
    assert changed_count([s3], [s3, e2]) == 1


def test_item_diff_shows_only_changed_parts():
    a = item()
    b = item(
        paras=[
            para("투입 자원", "현재 헬기 29대가 투입되어 있습니다.", "집중 투입하십시오."),
            para("추가 동원", "", "인접 시군에 헬기를 요청하십시오.", "요청"),
        ],
        rows=("지금: **박곡리**(대피 완료)",),
    )
    d = item_diff(a, b, "CHANGED")
    assert d["finding"] is None  # 판정 한 줄은 같음
    assert [(p["change"], p["topic"]) for p in d["proposals"]] == [
        ("MODIFIED", "투입 자원"),
        ("ADDED", "추가 동원"),
    ]
    assert d["proposals"][1]["before"] is None
    assert d["proposals"][1]["after"]["category"] == "REQUEST"  # 문단의 「요청」 → 영문 코드
    assert d["rows"] == {"added": ["지금: 박곡리(대피 완료)"], "removed": ["지금: 박곡리(28명)"]}
    assert d["categories"] == {"before": ["IMMEDIATE"], "after": ["IMMEDIATE"]}


def test_item_diff_new_item_and_failed_note():
    failed = Item(
        "E5", [], [{"title": "대응 제안", "blocks": [{"type": "failed", "text": "검사 실패"}]}], {}
    )
    d = item_diff(None, failed, "NEW")
    assert d["name"] == "재난문자·방송 전파"  # detail에 이름이 없으면 엔진 상수
    assert d["categories"] == {"before": None, "after": []}
    assert d["note"] == "검사 실패"


def test_table_rows_in_head_order():
    table = {
        "type": "table",
        "rows": [
            {"group": "군", "agency": "제50사단", "ask": "헬기", "item": "진화", "basis": "p.79"}
        ],
    }
    assert rows_of([{"blocks": [table]}]) == ["군 · 제50사단 · 헬기 · 진화 · p.79"]
