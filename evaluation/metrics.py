"""Spatial agreement metrics between a predicted and an observed burned area.

Both inputs are boolean rasters on the same grid (True = burned).
Naming used across the literature:
  IoU = Jaccard, F1 = Sørensen = Dice, precision = "burn probability",
  recall = "detection skill" (Filippi et al. 2014), F2 = core metric of NIFoS report 1207.
Undefined ratios (e.g. both rasters empty) return NaN instead of raising.
"""

from dataclasses import asdict, dataclass

import numpy as np

NAN = float("nan")


@dataclass(frozen=True)
class Confusion:
    tp: int
    fp: int
    fn: int
    tn: int

    @classmethod
    def of(cls, pred: np.ndarray, truth: np.ndarray) -> "Confusion":
        if pred.shape != truth.shape:
            raise ValueError(f"shape mismatch: {pred.shape} vs {truth.shape}")
        p = pred.astype(bool)
        t = truth.astype(bool)
        return cls(
            tp=int(np.count_nonzero(p & t)),
            fp=int(np.count_nonzero(p & ~t)),
            fn=int(np.count_nonzero(~p & t)),
            tn=int(np.count_nonzero(~p & ~t)),
        )


def _ratio(num: float, den: float) -> float:
    return num / den if den else NAN


def iou(c: Confusion) -> float:
    return _ratio(c.tp, c.tp + c.fp + c.fn)


def precision(c: Confusion) -> float:
    return _ratio(c.tp, c.tp + c.fp)


def recall(c: Confusion) -> float:
    return _ratio(c.tp, c.tp + c.fn)


def f_beta(c: Confusion, beta: float) -> float:
    b2 = beta * beta
    return _ratio((1 + b2) * c.tp, (1 + b2) * c.tp + b2 * c.fn + c.fp)


def kappa(c: Confusion) -> float:
    n = c.tp + c.fp + c.fn + c.tn
    if not n:
        return NAN
    po = (c.tp + c.tn) / n
    pe = ((c.tp + c.fp) * (c.tp + c.fn) + (c.fn + c.tn) * (c.fp + c.tn)) / (n * n)
    return _ratio(po - pe, 1 - pe)


def adi(c: Confusion) -> tuple[float, float, float]:
    """Area Difference Index (Duff et al. 2016): (total, over, under) = ((FP+FN), FP, FN) / TP."""
    return _ratio(c.fp + c.fn, c.tp), _ratio(c.fp, c.tp), _ratio(c.fn, c.tp)


def area_ratio(c: Confusion) -> float:
    """Predicted area / observed area (>1 means overprediction)."""
    return _ratio(c.tp + c.fp, c.tp + c.fn)


@dataclass(frozen=True)
class Scores:
    iou: float
    f1: float
    f2: float
    precision: float
    recall: float
    kappa: float
    adi: float
    adi_over: float
    adi_under: float
    area_ratio: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def score(pred: np.ndarray, truth: np.ndarray) -> Scores:
    c = Confusion.of(pred, truth)
    adi_total, adi_over, adi_under = adi(c)
    return Scores(
        iou=iou(c),
        f1=f_beta(c, 1.0),
        f2=f_beta(c, 2.0),
        precision=precision(c),
        recall=recall(c),
        kappa=kappa(c),
        adi=adi_total,
        adi_over=adi_over,
        adi_under=adi_under,
        area_ratio=area_ratio(c),
    )


def skill(model: float, reference: float) -> float:
    """Skill score relative to a baseline: (S_model - S_ref) / (1 - S_ref)."""
    return _ratio(model - reference, 1 - reference)
