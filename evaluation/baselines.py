"""Reference predictions that ELMFIRE has to beat.

B2 wind ellipse: an ellipse with the ignition at its rear focus, pointing downwind
of the mean wind, length-to-breadth ratio from wind speed (Anderson 1983).
Under the area-matched cut its area equals the observed area by construction, so
the comparison isolates shape and direction — "what does the physics add beyond wind?".
"""

import math

import numpy as np

# Midflame / 20-ft wind adjustment factor for sheltered fuels (Rothermel-style WAF).
# v0 assumption, same for every case.
WIND_ADJ_FACTOR = 0.4
MAX_LB = 8.0


def mean_wind(ws: list[float], wd_from_deg: list[float]) -> tuple[float, float]:
    """Vector-mean wind: (speed, direction the fire is pushed TOWARD, deg clockwise from north)."""
    u = sum(s * math.sin(math.radians(d + 180)) for s, d in zip(ws, wd_from_deg, strict=True))
    v = sum(s * math.cos(math.radians(d + 180)) for s, d in zip(ws, wd_from_deg, strict=True))
    n = len(ws)
    speed = math.hypot(u, v) / n
    toward = math.degrees(math.atan2(u, v)) % 360
    return speed, toward


def length_to_breadth(ws_20ft_mph: float) -> float:
    """Anderson (1983) LB from midflame wind speed (mph)."""
    u = ws_20ft_mph * WIND_ADJ_FACTOR
    lb = 0.936 * math.exp(0.2566 * u) + 0.461 * math.exp(-0.1548 * u) - 0.397
    return min(max(lb, 1.0), MAX_LB)


def wind_ellipse(
    shape: tuple[int, int],
    transform,
    ignition_xy: tuple[float, float],
    area_m2: float,
    ws_mph: list[float],
    wd_from_deg: list[float],
) -> np.ndarray:
    """Boolean mask of the B2 ellipse with the given area."""
    speed, toward = mean_wind(ws_mph, wd_from_deg)
    lb = length_to_breadth(speed)
    a = math.sqrt(area_m2 * lb / math.pi)
    b = a / lb
    c = math.sqrt(max(a * a - b * b, 0.0))
    th = math.radians(toward)
    ux, uy = math.sin(th), math.cos(th)  # unit vector downwind (x east, y north)
    cx, cy = ignition_xy[0] + c * ux, ignition_xy[1] + c * uy

    rows, cols = np.indices(shape)
    xs = transform.c + (cols + 0.5) * transform.a
    ys = transform.f + (rows + 0.5) * transform.e
    dx, dy = xs - cx, ys - cy
    along = dx * ux + dy * uy
    across = -dx * uy + dy * ux
    return (along / a) ** 2 + (across / b) ** 2 <= 1.0
