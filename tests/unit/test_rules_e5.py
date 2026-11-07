"""E5: 읍면 Go · Set(J9) · 직전 대비 변화 · 문자 기록(핵심 경우만)."""

from rules_example import VILLAGES, inputs

from rules.bundles import build_bundles
from rules.context import build_context
from rules.core.villages import VillageField
from rules.items.e5 import e5_input
from rules.sections.e5 import e5_sections


def _run(**kw):
    bundles = build_bundles(build_context(inputs(villages=VILLAGES, **kw)))
    e5 = e5_input(bundles)
    return bundles, e5, e5_sections(e5)


def test_first_proposal_all_new():
    bundles, e5, sec = _run(field={2: VillageField(alert_at="12:40")})
    areas = bundles["alerts"]["areas"]
    assert [(a["emd"], a["level"], a["change"]) for a in areas] == [
        ("안평면", "go", "new"),
        ("의성읍", "go", "new"),
        ("옥산면", "set", "new"),
    ]
    assert areas[0]["villages"] == ["괴산리", "신월리", "신안리"]
    assert bundles["alerts"]["log"] == [
        {"village": "신월리", "emd": "안평면", "at": "12:40", "kind": "대피 문자"}
    ]
    assert e5["status"] == "changed"
    assert e5["finding"] == (
        "Go: 안평면(괴산리·신월리·신안리) · 의성읍(철파리) / Set: 옥산면(정자리)"
        " / 바뀐 곳: 모두 새로 들어옴"
    )
    rows = sec["sections"][0]["blocks"][1]["rows"]
    assert rows[1]["text"] == "**Set 단계**: 옥산면(정자리)"
    assert [b["id"] for b in sec["sections"][0]["blocks"] if b["type"] == "action"] == [
        "E5-a1",
        "E5-a2",
    ]


def test_change_against_previous_and_removed():
    prev = [("의성군", "안평면", "go"), ("의성군", "옥산면", "go"), ("의성군", "단촌면", "set")]
    bundles, e5, _ = _run(prev_alerts=prev)
    changes = {a["emd"]: a["change"] for a in bundles["alerts"]["areas"]}
    assert changes == {"안평면": "same", "의성읍": "new", "옥산면": "down"}
    assert bundles["alerts"]["removed"] == [{"sigungu": "의성군", "emd": "단촌면", "prev": "set"}]
    assert "단촌면(set → 범위 밖)" in e5["finding"]
