"""Quality grading of observed (ground-truth) fire perimeters.

A wrong ground truth makes the metrics measure data errors instead of model skill.
In the AI Hub set 14/101 final perimeters self-intersect and many disagree with the
recorded burned area, so every case is graded before it is used for evaluation:

  A  usable as is
  B  usable with care (repaired geometry, moderate area mismatch, ignition near boundary)
  C  exclude from scoring (too few vertices, large area mismatch, ignition far outside)
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum

from shapely import union_all
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid

from spread.case import LonLat
from spread.geo import to_utm, utm_epsg

M2_PER_HA = 10_000

# Grade A: repaired area within this ratio of the recorded area, ignition this close
AREA_RATIO_A = (0.8, 1.25)
IGNITION_DIST_A_M = 50.0
# Grade C beyond these
AREA_RATIO_C = (0.5, 2.0)
IGNITION_DIST_C_M = 100.0


class Grade(StrEnum):
    """Ordered by severity: max(Grade.A, Grade.B) == Grade.B."""

    A = "A"
    B = "B"
    C = "C"


@dataclass
class PerimeterCheck:
    grade: Grade
    epsg: int
    vertices: int
    self_intersecting: bool
    raw_area_ha: float
    repaired_area_ha: float
    recorded_area_ha: float | None
    area_ratio: float | None
    ignition_distance_m: float
    duration_h: float | None
    reasons: list[str] = field(default_factory=list)
    repaired_utm: BaseGeometry | None = field(default=None, repr=False)

    def as_row(self) -> dict:
        row = asdict(self)
        row.pop("repaired_utm")
        row["grade"] = self.grade.value
        row["reasons"] = "; ".join(self.reasons)
        return row


def _polygonal(geom: BaseGeometry) -> BaseGeometry:
    """Keep only the areal part of make_valid output (it may add stray lines/points)."""
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    return union_all(parts) if parts else Polygon()


def check_perimeter(
    ring: list[LonLat],
    ignition: LonLat,
    recorded_area_ha: float | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> PerimeterCheck:
    lon, lat = ignition
    epsg = utm_epsg(lon, lat)
    vertices = max(len(ring) - 1, 0) if ring and ring[0] == ring[-1] else len(ring)
    duration_h = (end - start).total_seconds() / 3600 if start and end else None
    reasons: list[str] = []

    if vertices < 3:
        return PerimeterCheck(
            grade=Grade.C,
            epsg=epsg,
            vertices=vertices,
            self_intersecting=False,
            raw_area_ha=0.0,
            repaired_area_ha=0.0,
            recorded_area_ha=recorded_area_ha,
            area_ratio=None,
            ignition_distance_m=float("inf"),
            duration_h=duration_h,
            reasons=["fewer than 3 vertices"],
        )

    raw = to_utm(Polygon(ring), epsg)
    self_intersecting = not raw.is_valid
    repaired = _polygonal(make_valid(raw)) if self_intersecting else raw
    repaired_area_ha = repaired.area / M2_PER_HA
    ignition_distance_m = repaired.distance(to_utm(Point(lon, lat), epsg))
    area_ratio = repaired_area_ha / recorded_area_ha if recorded_area_ha else None

    grade = Grade.A
    if self_intersecting:
        grade = Grade.B
        reasons.append("self-intersecting (repaired)")
    if area_ratio is not None:
        if not AREA_RATIO_C[0] <= area_ratio <= AREA_RATIO_C[1]:
            grade = Grade.C
            reasons.append(f"area ratio {area_ratio:.2f} outside {AREA_RATIO_C}")
        elif not AREA_RATIO_A[0] <= area_ratio <= AREA_RATIO_A[1]:
            grade = max(grade, Grade.B)
            reasons.append(f"area ratio {area_ratio:.2f} outside {AREA_RATIO_A}")
    if ignition_distance_m > IGNITION_DIST_C_M:
        grade = Grade.C
        reasons.append(f"ignition {ignition_distance_m:.0f} m outside perimeter")
    elif ignition_distance_m > IGNITION_DIST_A_M:
        grade = max(grade, Grade.B)
        reasons.append(f"ignition {ignition_distance_m:.0f} m outside perimeter")

    return PerimeterCheck(
        grade=grade,
        epsg=epsg,
        vertices=vertices,
        self_intersecting=self_intersecting,
        raw_area_ha=raw.area / M2_PER_HA,
        repaired_area_ha=repaired_area_ha,
        recorded_area_ha=recorded_area_ha,
        area_ratio=area_ratio,
        ignition_distance_m=ignition_distance_m,
        duration_h=duration_h,
        reasons=reasons,
        repaired_utm=repaired,
    )
