from datetime import datetime

import pytest

from evaluation.datasets.validate import Grade, check_perimeter

# ~200 m x 200 m square around (128.0E, 35.0N) -> ~4 ha, closed ring of (lon, lat)
SQUARE = [(128.0, 35.0), (128.0022, 35.0), (128.0022, 35.0018), (128.0, 35.0018), (128.0, 35.0)]
# Same corners in "bow-tie" order -> self-intersecting
BOWTIE = [(128.0, 35.0), (128.0022, 35.0018), (128.0022, 35.0), (128.0, 35.0018), (128.0, 35.0)]
INSIDE = (128.0011, 35.0009)


def test_clean_square_is_grade_a():
    c = check_perimeter(
        SQUARE,
        INSIDE,
        recorded_area_ha=4.0,
        start=datetime(2025, 4, 7, 12),
        end=datetime(2025, 4, 7, 17, 30),
    )
    assert c.grade is Grade.A
    assert c.epsg == 32652
    assert c.vertices == 4
    assert not c.self_intersecting
    assert c.repaired_area_ha == pytest.approx(4.0, rel=0.05)
    assert c.ignition_distance_m == 0.0
    assert c.duration_h == pytest.approx(5.5)


def test_self_intersection_is_repaired_and_downgraded():
    c = check_perimeter(BOWTIE, INSIDE)
    assert c.self_intersecting
    assert c.grade is Grade.B
    assert c.raw_area_ha == pytest.approx(0.0, abs=0.01)  # lobes cancel out in the raw ring
    assert c.repaired_area_ha == pytest.approx(2.0, rel=0.05)  # two triangles


@pytest.mark.parametrize(
    ("recorded", "grade"),
    [(4.0, Grade.A), (3.0, Grade.B), (6.0, Grade.B), (1.5, Grade.C), (10.0, Grade.C)],
)
def test_area_ratio_grades(recorded, grade):
    assert check_perimeter(SQUARE, INSIDE, recorded_area_ha=recorded).grade is grade


def test_ignition_distance_grades():
    near = (128.0022 + 0.0008, 35.0009)  # ~73 m east of the square
    far = (128.0022 + 0.003, 35.0009)  # ~270 m east
    assert check_perimeter(SQUARE, near, recorded_area_ha=4.0).grade is Grade.B
    assert check_perimeter(SQUARE, far, recorded_area_ha=4.0).grade is Grade.C


def test_too_few_vertices_is_grade_c():
    c = check_perimeter([(128.0, 35.0), (128.001, 35.0)], INSIDE)
    assert c.grade is Grade.C
    assert c.repaired_utm is None


def test_grade_ordering():
    assert max(Grade.A, Grade.B) is Grade.B
    assert max(Grade.B, Grade.C) is Grade.C
