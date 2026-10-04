"""Start a run from an observed fire perimeter instead of the ignition point (plan II-1).

ELMFIRE reads an initial level-set field phi: cells with phi <= 0 start burned
(time of arrival 0) and, when any phi > 0 exists, the phi grid is used as the ignition
source (elmfire_init.f90, elmfire_level_set.f90). So: -1 inside the perimeter, +1 outside,
and no point ignition.

This is how the service runs every hour: the situation reporter enters the current
perimeter and the prediction restarts from it.
"""

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

INSIDE, OUTSIDE = -1.0, 1.0


def phi_from_polygon(
    polygon_utm: BaseGeometry, xmin: float, ymax: float, cell: float, shape: tuple[int, int]
) -> np.ndarray:
    """phi raster on a north-up grid: cell centres inside the polygon get -1, others +1.

    A perimeter smaller than one cell still burns the cell that contains its centroid,
    so a tiny start never vanishes.
    """
    rows, cols = np.indices(shape)
    xs = xmin + (cols + 0.5) * cell
    ys = ymax - (rows + 0.5) * cell
    inside = shapely.contains_xy(polygon_utm, xs, ys)
    if not inside.any():
        c = polygon_utm.centroid
        r, k = int((ymax - c.y) // cell), int((c.x - xmin) // cell)
        if 0 <= r < shape[0] and 0 <= k < shape[1]:
            inside[r, k] = True
    return np.where(inside, INSIDE, OUTSIDE)


def ignition_block(x: float | None, y: float | None) -> str:
    """&SIMULATOR ignition lines: one point ignition, or none when starting from phi."""
    if x is None or y is None:
        return "NUM_IGNITIONS = 0"
    return f"NUM_IGNITIONS = 1\nX_IGN(1)      = {x}\nY_IGN(1)      = {y}\nT_IGN(1)      = 0.0"
