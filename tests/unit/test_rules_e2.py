"""E2: 밤 · 현장 입력 · 예상 시각 경과가 마을 묶음에 붙고, E2는 고르기만 하는지(핵심 경우만)."""

from datetime import datetime

from rules_example import VILLAGES, inputs

from rules.bundles import build_bundles
from rules.context import build_context
from rules.core.villages import VillageField
from rules.items.e2 import e2_input
from rules.sections.e2 import e2_sections

ALERT = {2: VillageField(alert_at="12:40", reported_at="12:40")}  # 신월리 12:40 대피 문자


def _run(**kw):
    bundles = build_bundles(build_context(inputs(villages=VILLAGES, **kw)))
    e2 = e2_input(bundles)
    return bundles, e2, e2_sections(e2)


def test_example_1250_sun_night_and_alert():
    bundles, e2, sec = _run(field=ALERT)
    sun = bundles["time"]["sun"]
    # 일몰 18:38은 P6(17:50 ~ 18:50) 안 → 5시간 안은 낮
    assert sun["sunset"] == {"at": "18:38", "p": 6, "zone": "p8"}
    assert sun["now_is_night"] is False
    f = e2["facts"]
    assert [r["name"] for r in f["immediate"]] == ["괴산리", "신월리", "신안리", "철파리"]
    assert f["immediate_total"] == 797
    # 정자리는 P8(19:50 ~ 20:50)에 닿음 → 밤
    (jeongja,) = f["pre_evac"]
    assert (jeongja["from"], jeongja["until"], jeongja["night"]) == ("19:50", "20:50", True)
    assert e2["status"] == "order_needed"
    assert e2["finding"] == (
        "위험구역 4곳 797명 / 잠재 위험구역 1곳 114명: 정자리(밤에 닿음) · 일몰 18:38"
    )
    # 목록은 사실만: 시각 구간 · 문자 발송 표시. 시한(「일몰 전」)은 쓰지 않음
    rows = sec["sections"][0]["blocks"][1]["rows"]
    assert rows[0]["text"].startswith("지금(이미 닿음): **괴산리**(147명), **신월리**(128명)")
    assert "신월리는 12:40 대피 문자가 나갔지만 대피 완료 보고가 없습니다." in rows[0]["text"]
    assert rows[1]["text"] == "12:50 ~ 13:50 닿을 수 있음: **신안리**(181명)"
    assert [b["id"] for b in sec["sections"][0]["blocks"] if b["type"] == "action"] == [
        "E2-a1",
        "E2-a2",
    ]


def test_night_now():
    # 0시가 22:00(밤)이면 지금도 밤, 이미 닿은 · 5시간 안 마을도 모두 밤에 닿음
    night = datetime(2025, 3, 22, 22, 0)
    bundles, e2, _ = _run(base_at=night, generated_at=night)
    assert bundles["time"]["sun"]["now_is_night"] is True
    assert bundles["time"]["sun"]["sunset"]["p"] is None  # 다음 일몰은 8시간 밖
    assert all(r["night"] for r in e2["facts"]["immediate"])


def test_passed_time_is_marked_in_correction_version():
    # 12:50 화선으로 14:10에 다시 만든 제안서: 신안리(~13:50)는 예상 시각이 지남
    _, e2, sec = _run(generated_at=datetime(2025, 3, 22, 14, 10))
    sinan = e2["facts"]["immediate"][2]
    assert (sinan["name"], sinan["passed"]) == ("신안리", True)
    assert "예상 시각 경과" in sec["sections"][0]["blocks"][1]["rows"][1]["text"]
