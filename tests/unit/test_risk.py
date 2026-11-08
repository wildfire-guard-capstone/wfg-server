import pytest

from app.services.risk import FACTORS, FactorGrade, aggregate, grade_factors


class FakePrediction:
    def __init__(self, id_):
        self.id = id_


def grades(value):
    return [FactorGrade(code, None, value, None, None, None) for code, *_ in FACTORS]


def test_weights_sum_to_094():
    assert len(FACTORS) == 23
    assert sum(f[3] for f in FACTORS) == pytest.approx(0.94)


def test_all_same_grade_gives_that_score_and_contributions_add_up():
    r = aggregate(grades(4))
    assert r["status"] == "READY" and r["score"] == 4.0
    assert r["included_weight_sum"] == pytest.approx(0.94)
    assert sum(f["contribution"] for f in r["factors"]) == pytest.approx(4.0, abs=1e-3)
    assert set(r["axes"].values()) == {4.0}


def test_missing_variables_leave_the_denominator():
    g = grades(5)
    g[0] = FactorGrade("slope_up", None, None, "자료 없음", None, None)  # 가중치 0.08
    r = aggregate(g)
    assert r["status"] == "PARTIAL" and r["score"] == 5.0  # 0.94로 나누지 않는다
    assert r["included_weight_sum"] == pytest.approx(0.86)
    slope = next(f for f in r["factors"] if f["code"] == "slope_up")
    assert slope["included"] is False and slope["contribution"] is None


def test_nothing_included_is_unavailable_not_zero():
    r = aggregate(grades(None))
    assert r["status"] == "UNAVAILABLE" and r["score"] is None


def test_fake_grades_are_stable_per_prediction():
    a, b = grade_factors(None, FakePrediction(41)), grade_factors(None, FakePrediction(41))
    assert [x.grade for x in a] == [x.grade for x in b]
    assert all(1 <= x.grade <= 5 and x.source == "FAKE" for x in a)
