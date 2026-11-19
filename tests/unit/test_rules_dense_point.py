"""주택 밀집 지점(11/20 사용자 결정 ①): 반경 300 m 안 같은 리 주택이 가장 많은 주택 점."""

from types import SimpleNamespace

from shapely.geometry import box

from rules.bundles.shelters import _dense_point


def test_dense_point_picks_densest_cluster_not_lonely_house_near_centroid():
    # 서쪽 무리 10동(-1,800 m 둘레), 동쪽 무리 3동(+2,000 m), 가운데 외딴 집 1동(0 m)
    xs = [-1800.0 + 10 * i for i in range(10)] + [2000.0, 2010.0, 2020.0] + [0.0]
    ys = [0.0] * len(xs)
    ctx = SimpleNamespace(inputs=SimpleNamespace(houses=(xs, ys)))
    at = _dense_point(ctx, box(-3000, -100, 3000, 100))
    # 옛 정의(무게중심 −857 m에 가장 가까운 집)라면 가운데 외딴 집(0 m)을 골랐다
    assert -1800 <= at.x <= -1710 and at.y == 0


def test_dense_point_tie_goes_to_centroid_and_none_without_houses():
    xs, ys = (
        [-500.0, 0.0, 500.0],
        [0.0, 0.0, 0.0],
    )  # 셋 다 300 m 안 이웃이 자기뿐 → 무게중심(0 m)에 가까운 집
    ctx = SimpleNamespace(inputs=SimpleNamespace(houses=(xs, ys)))
    assert _dense_point(ctx, box(-1000, -10, 1000, 10)).x == 0.0
    assert _dense_point(ctx, box(5000, 5000, 6000, 6000)) is None
