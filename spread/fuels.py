"""Case-level fuel and fuel moisture from AI Hub observation points (plan I-1, I-3).

Each AI Hub case has a few to several hundred observation points with a fuel type
(침엽수림 / 활엽수림 / 혼효림 / 초지 / 관목 / 무립목지·비산림) and a fuel moisture (%).
Until the spatial fuel map (임상도, plan I-7) and land cover (plan I-2) arrive, the case
gets one fuel code: the majority fuel type mapped through `data/crosswalk_v1.csv`.

The crosswalk is our estimate (no official 임상 → Scott & Burgan 40 table exists).
"""

import csv
import statistics
from collections import Counter
from pathlib import Path

CROSSWALK_V1 = Path(__file__).parent / "data" / "crosswalk_v1.csv"
NON_BURNABLE = range(91, 100)


def load_crosswalk(path: Path = CROSSWALK_V1) -> dict[str, int]:
    with path.open(encoding="utf-8") as f:
        return {r["fuel_type"]: int(r["fbfm40"]) for r in csv.DictReader(f)}


def majority_fuel(points: list[dict], crosswalk: dict[str, int], default: int) -> int:
    """FBFM40 of the most common burnable fuel type among the points.

    Non-burnable point labels are ignored: one cell of the case is a road or a house,
    the fire still spread through the vegetation around it.
    """
    burnable = [
        crosswalk[p["fuel_type"]]
        for p in points
        if p.get("fuel_type") in crosswalk and crosswalk[p["fuel_type"]] not in NON_BURNABLE
    ]
    if not burnable:
        return default
    return Counter(burnable).most_common(1)[0][0]


def dead_fuel_moisture(points: list[dict], default: dict[str, float]) -> dict[str, float]:
    """m1 = median point fuel moisture (0 or blank = missing), m10 = m1 + 1, m100 = m1 + 2.

    The +1 / +2 steps for 10-h and 100-h fuels follow the usual NWCG rule of thumb.
    Live moisture (lh, lw) keeps the default. Falls back to `default` when no point has
    a value.
    """
    values = [float(p["fuel_moisture"]) for p in points if p.get("fuel_moisture")]
    values = [v for v in values if v > 0]
    if not values:
        return dict(default)
    m1 = statistics.median(values)
    return {**default, "m1": m1, "m10": m1 + 1, "m100": m1 + 2}


def load_points(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))
