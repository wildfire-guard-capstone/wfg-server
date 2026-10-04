"""Wind ensemble: run ELMFIRE several times with perturbed wind and combine (plan I-5).

The station wind often does not represent the wind at the fire (e.g. OC20250323 in
Okcheon uses the Bonghwa station). Instead of trusting one direction, members shift the
whole wind direction series by a random offset and scale the speed, and the burned
masks are averaged into a burn probability.

Member 0 is always the unperturbed run, so the ensemble contains the deterministic
prediction. Offsets are constant in time on purpose: the error we are modelling is a
systematic station bias, not hour-to-hour noise.
"""

import random

import numpy as np


def perturb(
    wx: list[tuple[float, float]],
    n: int,
    wd_sigma_deg: float,
    ws_frac: float,
    seed: int = 0,
) -> list[list[tuple[float, float]]]:
    """n members of the hourly (ws, wd) series. Member 0 = unchanged."""
    rng = random.Random(seed)
    members = [list(wx)]
    for _ in range(n - 1):
        offset = rng.gauss(0.0, wd_sigma_deg)
        scale = rng.uniform(1 - ws_frac, 1 + ws_frac)
        members.append([(ws * scale, (wd + offset) % 360) for ws, wd in wx])
    return members


def burn_probability(masks: list[np.ndarray]) -> np.ndarray:
    return np.mean(np.stack([m.astype(float) for m in masks]), axis=0)


def top_cells(
    prob: np.ndarray, target_cells: int, tiebreak: np.ndarray | None = None
) -> np.ndarray:
    """The target_cells most probable cells (area-matched selection on a probability map).

    Ties are broken by `tiebreak` (smaller first, e.g. mean arrival time), then by index,
    so the result is deterministic.
    """
    flat = prob.ravel()
    order_keys = [-flat]
    if tiebreak is not None:
        order_keys.insert(0, np.nan_to_num(tiebreak.ravel(), nan=np.inf))
    order = np.lexsort(order_keys)  # last key is primary
    mask = np.zeros(flat.size, dtype=bool)
    mask[order[: min(target_cells, int(np.count_nonzero(flat > 0)))]] = True
    return mask.reshape(prob.shape)
