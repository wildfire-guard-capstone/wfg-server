# ruff: noqa: E501
"""데모 회차 보정 방법(blend = 직전 구간 · 발화 뒤 평균 속도 배율의 기하평균)과 scenario.json 읽기."""

import math

import pytest
from shapely.geometry import box

from demo.uiseong import rounds, step1_spread
from spread import perimeter_run as pr

SCENARIO = {
    "incident": {"name": "시험 산불", "sigungu": "의성군"},
    "time": {"date": "2025-03-22", "ignition_at": "11:24", "elmfire_band1": "11:00"},
    "rounds": [
        {
            "no": n,
            "version": f"v{n}.1",
            "at": at,
            "report": f"{n}보",
            "reader": "의성군수",
            "deployed": {
                "reported_at": at,
                "helicopter": {"산림청": 5},
                "crew": {"진화대": 81},
                "vehicle": {},
            },
            "weather": {"wind_dir_deg": 270.0, "wind_ms": 3.4, "temp_c": 22.9, "rh_pct": 22},
            "villages": {"신월리": {"alert_at": "12:40", "reported_at": at}} if n == 1 else {},
        }
        for n, at in ((1, "12:50"), (2, "13:10"))
    ],
}
SC = rounds.parse(SCENARIO)


class FakeMap:
    def report(self, at):
        return box(0, 0, 1000, 1000)  # 100 ha


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(deck, work, start, t_from, t_to, observed, target_ha=None, **kw):
        kind = "rate" if target_ha is not None else "span"
        seen.append((kind, t_from, t_to, target_ha))
        adj = {"rate": 1.0, "span": 4.0}[kind]
        return pr.Calibration(adj, target_ha or 120.0, 118.0, t_from, t_to, [(adj, 118.0)])

    monkeypatch.setattr(step1_spread.pr, "calibrate", fake)
    return seen


def test_scenario_parse():
    r1 = SC.rounds[0]
    assert r1.t_s == 6600.0 and SC.ignition_t_s == 1440.0  # 12:50 · 11:24, 밴드 1 = 11:00
    assert (
        r1.deployed.helicopter == {"산림청": 5} and r1.field_by_name["신월리"].alert_at == "12:40"
    )


def test_blend_is_geometric_mean(calls, tmp_path):
    rd = SC.rounds[1]  # 13:10
    cal = step1_spread.calibrate_round(None, tmp_path, FakeMap(), rd, SC, "blend")
    assert cal["method"] == "blend" and math.isclose(cal["adj"], 2.0)
    assert [c[0] for c in calls] == ["span", "rate"]
    span, rate = calls
    assert span[1:3] == (SC.rounds[0].t_s, rd.t_s)  # 12:50 1보 → 13:10 2보
    hours = (rd.t_s - SC.ignition_t_s) / 3600
    assert math.isclose(rate[3], 100 + 100 / hours)  # 2보 화선 + 발화 뒤 평균 1시간
    assert [p["adj"] for p in cal["parts"]] == [4.0, 1.0]


def test_first_round_is_rate_only(calls, tmp_path):
    cal = step1_spread.calibrate_round(None, tmp_path, FakeMap(), SC.rounds[0], SC, "blend")
    assert cal["method"] == "rate" and cal["adj"] == 1.0 and len(cal["parts"]) == 1


def test_unknown_method(calls, tmp_path):
    with pytest.raises(ValueError):
        step1_spread.calibrate_round(None, tmp_path, FakeMap(), SC.rounds[1], SC, "mean")
