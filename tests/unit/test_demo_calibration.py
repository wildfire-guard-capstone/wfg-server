"""데모 회차 보정 방법(blend = 직전 구간 · 발화 뒤 평균 속도 배율의 기하평균)."""

import math

import pytest
from shapely.geometry import box

from demo.uiseong import run_demo
from demo.uiseong.rounds import ROUNDS
from spread import perimeter_run as pr


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

    monkeypatch.setattr(run_demo.pr, "calibrate", fake)
    return seen


def test_blend_is_geometric_mean(calls, tmp_path):
    rd = ROUNDS[1]  # 13:10
    cal = run_demo.calibrate_round(None, tmp_path, FakeMap(), rd, "blend")
    assert cal["method"] == "blend" and math.isclose(cal["adj"], 2.0)
    assert [c[0] for c in calls] == ["span", "rate"]
    span, rate = calls
    assert span[1:3] == (ROUNDS[0].t_s, rd.t_s)  # 12:50 1보 → 13:10 2보
    hours = (rd.t_s - run_demo.IGNITION_T_S) / 3600
    assert math.isclose(rate[3], 100 + 100 / hours)  # 2보 화선 + 발화 뒤 평균 1시간
    assert [p["adj"] for p in cal["parts"]] == [4.0, 1.0]


def test_first_round_is_rate_only(calls, tmp_path):
    cal = run_demo.calibrate_round(None, tmp_path, FakeMap(), ROUNDS[0], "blend")
    assert cal["method"] == "rate" and cal["adj"] == 1.0 and len(cal["parts"]) == 1


def test_unknown_method(calls, tmp_path):
    with pytest.raises(ValueError):
        run_demo.calibrate_round(None, tmp_path, FakeMap(), ROUNDS[1], "mean")
