import numpy as np
import pytest

from evaluation import compare
from spread import ensemble

WX = [(10.0, 270.0), (12.0, 280.0), (8.0, 300.0)]


def test_member_zero_is_unperturbed_and_seed_reproducible():
    a = ensemble.perturb(WX, 5, wd_sigma_deg=30, ws_frac=0.2, seed=7)
    b = ensemble.perturb(WX, 5, wd_sigma_deg=30, ws_frac=0.2, seed=7)
    assert len(a) == 5
    assert a[0] == WX
    assert a == b


def test_offsets_are_constant_in_time():
    m = ensemble.perturb(WX, 3, wd_sigma_deg=30, ws_frac=0.2, seed=1)[1]
    offsets = {round((wd - base) % 360, 6) for (_, wd), (_, base) in zip(m, WX, strict=True)}
    scales = {round(ws / base, 6) for (ws, _), (base, _) in zip(m, WX, strict=True)}
    assert len(offsets) == 1 and len(scales) == 1
    assert 0.8 <= scales.pop() <= 1.2


def test_burn_probability_and_top_cells():
    a = np.array([[1, 1, 0], [0, 0, 0]], dtype=bool)
    b = np.array([[1, 0, 1], [0, 0, 0]], dtype=bool)
    prob = ensemble.burn_probability([a, b])
    assert prob.tolist() == [[1.0, 0.5, 0.5], [0.0, 0.0, 0.0]]
    # two cells: the certain one + the earlier-arriving tie
    toa = np.array([[0.0, 900.0, 100.0], [np.nan, np.nan, np.nan]])
    sel = ensemble.top_cells(prob, 2, tiebreak=toa)
    assert sel.tolist() == [[True, False, True], [False, False, False]]


def test_top_cells_never_selects_zero_probability():
    prob = np.array([[0.5, 0.0], [0.0, 0.0]])
    assert ensemble.top_cells(prob, 3).sum() == 1


def test_compare_adoption_rule():
    base = {f"C{i}": 0.30 for i in range(20)}
    better = {c: v + (0.05 if i < 12 else -0.01) for i, (c, v) in enumerate(base.items())}
    r = compare.compare(base, better)
    assert r["wins"] == 12 and r["losses"] == 8
    assert r["median_delta"] == pytest.approx(0.05)
    assert r["adopt"]
    worse = {c: v - 0.02 for c, v in base.items()}
    assert not compare.compare(base, worse)["adopt"]
