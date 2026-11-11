import pytest
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, box

from app.repositories.predictions import check_slices, to_multipolygon

START = Point(0, 0)


def squares(n=8):
    return [box(-100 * h, -100 * h, 100 * h, 100 * h) for h in range(1, n + 1)]


def test_check_slices_passes_nested_ranges():
    out = check_slices(START, squares())
    assert len(out) == 8
    assert all(isinstance(g, MultiPolygon) for g in out)


def test_check_slices_needs_eight():
    with pytest.raises(ValueError, match="7개"):
        check_slices(START, squares(7))


def test_check_slices_rejects_shrinking_range():
    slices = squares()
    slices[4] = box(-10, -10, 10, 10)
    with pytest.raises(ValueError, match="P5가 앞 범위"):
        check_slices(START, slices)


def test_check_slices_start_point_must_be_covered():
    with pytest.raises(ValueError, match="P1가 앞 범위"):
        check_slices(Point(5000, 5000), squares())


def test_check_slices_point_start_allows_simplify_width():
    """ELMFIRE 면은 칸 경계를 다듬어서 발화점이 경계에서 조금 떨어질 수 있다(10 m까지 봐준다)."""
    assert len(check_slices(Point(107.7, 0), squares())) == 8  # P1 경계에서 7.7 m 밖(11/11 EC2)
    with pytest.raises(ValueError, match="P1가 앞 범위"):
        check_slices(Point(120, 0), squares())  # 20 m 밖이면 안 덮은 것


def test_check_slices_polygon_start_allows_small_edge_error():
    start = box(-100, -100, 100, 100.05)  # P1 밖으로 0.025% 삐져나옴(래스터 경계 오차)
    assert len(check_slices(start, squares())) == 8


def test_to_multipolygon_drops_lines_and_rejects_empty():
    mixed = GeometryCollection([box(0, 0, 1, 1), LineString([(0, 0), (5, 5)])])
    assert to_multipolygon(mixed).area == 1
    with pytest.raises(ValueError, match="면이 아닌"):
        to_multipolygon(LineString([(0, 0), (1, 1)]))
