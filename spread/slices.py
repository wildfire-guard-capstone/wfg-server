"""Cut burned areas out of an ELMFIRE time-of-arrival (TOA) raster.

One ELMFIRE run gives one TOA raster (seconds after ignition, NODATA = not burned).
Every product is a threshold on it:
  P1..P8           TOA <= t x 3600 s         (service output, must be nested)
  area-matched     TOA <= t*, burned area = observed area   (Filippi et al. 2014)
  observed duration TOA <= end - start        (speed check; overpredicts, no suppression)
"""

import numpy as np

HOUR_S = 3600.0


def burned(toa: np.ndarray, t_s: float) -> np.ndarray:
    return np.isfinite(toa) & (toa >= 0) & (toa <= t_s)


def hourly(toa: np.ndarray, hours: int = 8) -> list[np.ndarray]:
    """P1..P{hours}. Nested by construction (a larger threshold keeps every cell)."""
    return [burned(toa, h * HOUR_S) for h in range(1, hours + 1)]


def area_matched(toa: np.ndarray, target_cells: int) -> tuple[np.ndarray, float | None]:
    """Burned area at the first time it reaches target_cells.

    Returns (mask, t_star_s). t_star is None when the simulation never reaches the
    target; the mask is then everything that burned (marked "not reached").
    """
    times = np.sort(toa[np.isfinite(toa) & (toa >= 0)])
    if times.size == 0:
        return np.zeros(toa.shape, dtype=bool), None
    if times.size < target_cells:
        return burned(toa, float(times[-1])), None
    t_star = float(times[target_cells - 1])
    return burned(toa, t_star), t_star
