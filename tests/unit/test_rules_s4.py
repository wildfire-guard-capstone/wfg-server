"""S4: 시간별 풍향 · 풍속 · 최대 풍속, 해(일몰이 드는 구간), S4는 옮기기만(핵심 경우만)."""

from datetime import datetime

import pytest
from rules_example import inputs

from rules.bundles import build_bundles
from rules.context import build_context
from rules.core.direction import compass8, compass16
from rules.items.s4 import s4_input
from rules.sections.s4 import s4_sections

WIND = {0: 3.4, 1: 5.0, 2: 8.0, 3: 8.0, 4: 8.0, 5: 5.0, 6: 4.0, 7: 3.0, 8: 2.0}
DEG = {**{h: 270.0 for h in range(9)}, 5: 247.5}  # 5시간 뒤만 서남서


def _run(**kw):
    bundles = build_bundles(build_context(inputs(wind_ms=WIND, wind_deg=DEG, **kw)))
    s4 = s4_input(bundles)
    return bundles, s4, s4_sections(s4)


def test_compass():
    assert (compass16(270), compass16(247.5), compass8(90), compass8(350)) == (
        "서",
        "서남서",
        "동",
        "북",
    )


def test_hourly_and_max_hours():
    bundles, s4, sec = _run(temp_c=22.9, rh_pct=22)
    w = bundles["weather"]
    assert w["max"] == {"ms": 8.0, "dir": "서", "hours": ["14:50", "15:50", "16:50"]}
    assert w["hourly"][5]["dir"] == "서남서"
    assert s4["facts"]["max"]["label"] == "14:50~16:50"
    assert s4["facts"]["sun"]["sunset_zone"] == "p8"  # 일몰 18:38은 P6 → 5시간 안은 낮
    assert s4["finding"] == (
        "지금 낮 · 일몰 18:38 · 일출 다음 날 06:26 · 최대 풍속 8 m/s(서, 14:50~16:50)"
    )
    assert s4["facts"]["now"]["temp_c"] == 22.9
    assert len([b for s in sec["sections"] for b in s["blocks"] if b["type"] == "action"]) == 3


def test_night_status_and_missing_direction_errors():
    night = datetime(2025, 3, 22, 22, 0)
    _, s4, _ = _run(base_at=night, generated_at=night)
    assert s4["status"] == "night"
    with pytest.raises(ValueError, match="풍향"):
        build_bundles(build_context(inputs(wind_ms=WIND, wind_deg={0: 270.0})))
