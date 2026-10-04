from shapely.geometry import Point, box

from spread.perimeter_start import INSIDE, OUTSIDE, ignition_block, phi_from_polygon


def test_phi_inside_negative_outside_positive():
    # 10 x 10 grid of 30 m cells; polygon covers cells (rows 2-4, cols 3-5)
    poly = box(90.0, 150.0, 180.0, 240.0)
    phi = phi_from_polygon(poly, xmin=0.0, ymax=300.0, cell=30.0, shape=(10, 10))
    assert phi[2:5, 3:6].tolist() == [[INSIDE] * 3] * 3
    assert (phi == INSIDE).sum() == 9
    assert phi[0, 0] == OUTSIDE


def test_tiny_perimeter_keeps_one_burning_cell():
    poly = Point(100.0, 100.0).buffer(2.0)  # far smaller than a cell, misses cell centres
    phi = phi_from_polygon(poly, xmin=0.0, ymax=300.0, cell=30.0, shape=(10, 10))
    assert (phi == INSIDE).sum() == 1
    assert phi[6, 3] == INSIDE  # row (300-100)//30 = 6, col 100//30 = 3


def test_ignition_block_variants():
    assert ignition_block(None, None) == "NUM_IGNITIONS = 0"
    point = ignition_block(1.5, 2.5)
    assert "NUM_IGNITIONS = 1" in point and "X_IGN(1)      = 1.5" in point
