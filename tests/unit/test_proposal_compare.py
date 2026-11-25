"""「변경」 판단(DB 없이): CHANGED · WORDING_ONLY · SAME · NEW · REMOVED와 바뀐 부분 고르기."""

from dataclasses import dataclass, field, replace
from typing import Any

from app.db.models.enums import ItemCategory
from app.services.proposal_compare import (
    change_of,
    changed_count,
    changes,
    item_diff,
    rows_of,
    table_row,
)
from rules.items.requests import HEAD, ROW_KEYS, _row


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


def test_fact_blocks_count_as_wording():
    """11/17 네 단계: 상황 · 상태 · 판단(fact 블록)은 LLM이 쓴 글이라 바뀌어도 「표현만」이다."""

    def with_fact(text: str) -> Item:
        a = item()
        fact = {"type": "fact", "step": "situation", "text": text}
        sec = a.sections[0] | {"blocks": [fact, *a.sections[0]["blocks"]]}
        return replace(a, sections=[sec])

    a, b = with_fact("헬기 23대가 투입되어 있습니다."), with_fact("지금 헬기 23대가 떠 있습니다.")
    assert change_of(a, b) == "WORDING_ONLY"
    assert change_of(a, with_fact("헬기 23대가 투입되어 있습니다.")) == "SAME"


def test_engine_side_changes():
    a = item()
    assert change_of(a, item(finding="투입: 헬기 29")) == "CHANGED"
    assert change_of(a, item(rows=("지금: **박곡리**(대피 완료)",))) == "CHANGED"
    assert change_of(a, replace(a, categories=[ItemCategory.STANDBY])) == "CHANGED"
    moved = item(
        paras=[para("투입 자원", "현재 헬기 29대가 투입되어 있습니다.", "집중 투입하십시오.")]
    )
    assert change_of(a, moved) == "CHANGED"


def test_engine_value_behind_the_same_finding_is_a_change():
    """11/25: 「대피명령」 정정은 판정 한 줄 · 목록에는 안 나오지만 엔진 값이 다르다."""

    def e2(order: bool, action: str, **more: Any) -> Item:
        a = item(code="E2", paras=[para("대피", "", action)])
        facts = {"immediate": [{"name": "구계리(구계2리)", "evac_order": order, **more}]}
        return replace(a, detail=a.detail | {"facts": facts})

    a = e2(False, "대피를 명령하십시오.")
    assert change_of(a, e2(True, "대피 여부를 확인하십시오.")) == "CHANGED"
    assert change_of(a, e2(False, "대피 여부를 확인하십시오.")) == "WORDING_ONLY"
    assert change_of(a, e2(True, "대피를 명령하십시오.")) == "SAME"
    # 보고 시각 · 직전 대비는 값이 아니다
    assert (
        change_of(a, e2(False, "확인하십시오.", reported_at="16:03", prev="go")) == "WORDING_ONLY"
    )


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
    # 요청 표 줄은 rules.items.requests._row가 만든 키(group · agency · target · when · item)
    row = _row("산림 헬기", "울진산림항공관리소", "진화헬기(발화지 반경 100 km 안)", "지금", "S3")
    table = {"type": "table", "head": HEAD, "rows": [row]}
    assert rows_of([{"blocks": [table]}]) == [
        "산림 헬기 · 울진산림항공관리소 · 진화헬기(발화지 반경 100 km 안) · 지금 · S3"
    ]


def test_table_row_fills_every_head_column():
    """QA 11/23: 옛 키(ask · basis)로 펼쳐 칸이 null · 어긋나면 화면이 제안서 전체를 거부한다."""
    row = _row("소방", "의성소방서", "괴산리 주택", "5시간 안", "S2")
    assert set(row) == set(ROW_KEYS) and len(ROW_KEYS) == len(HEAD)
    assert table_row(row) == ["소방", "의성소방서", "괴산리 주택", "5시간 안", "S2"]
    assert all(isinstance(v, str) for v in table_row(row))


def test_detail_rows_count_as_engine_side():
    """11/18: 제안 문단에 붙은 자세한 자료(details)의 목록 줄이 바뀌면 「변경」이다."""

    def with_detail(row: str) -> Item:
        a = item(rows=())
        p = a.sections[0]["blocks"][0] | {"details": [{"type": "list", "rows": [{"text": row}]}]}
        return replace(a, sections=[{"title": "", "blocks": [p]}])

    assert rows_of(with_detail("**괴산리**(147명)").sections) == ["괴산리(147명)"]
    assert change_of(with_detail("괴산리(147명)"), with_detail("괴산리(150명)")) == "CHANGED"


def test_api_view_formats_detail_tables_like_top_level():
    """11/18: PROP-02 응답에서 details의 표도 열 순서 배열로, 문단 구분은 영문 코드로."""
    from app.services.proposal_view import sections_out

    para = {
        "type": "paragraph",
        "category": "즉시",
        "body": "회의를 여십시오.",
        "action": "S6-a1",
        "quote_ok": True,
        "details": [
            {
                "type": "table",
                "head": ["관련 기관"],
                "rows": [{"group": "소방", "agency": "의성소방서"}],
            },
            {"type": "list", "style": "bullet", "rows": [{"text": "괴산리", "id": "v1"}]},
        ],
    }
    (sec,) = sections_out([{"title": "", "blocks": [para]}])
    out = sec["blocks"][0]
    assert out["category"] == "IMMEDIATE" and "action" not in out and "quote_ok" not in out
    assert out["details"][0]["rows"] == [["소방", "의성소방서", None, None, None]]
    assert out["details"][1] == {"type": "list", "style": "bullet", "rows": [{"text": "괴산리"}]}
