from rules.area import Crossing, first_over

# 12:50 예시: P1 ~ P8 면적(ha)
SLICES = [(1, 89), (2, 205), (3, 262), (4, 316), (5, 363), (6, 410), (7, 455), (8, 499)]


def test_example_crosses_100_at_p2_and_never_1000():
    assert first_over(48.5, SLICES, [100, 100, 1000]) == [Crossing(100, 2, 205)]


def test_passed_threshold_is_skipped_and_bound_is_inclusive():
    slices = [(1, 10), (2, 100)]
    assert first_over(12, slices, [10, 100]) == [Crossing(100, 2, 100)]
