"""방위: 각도(북 = 0°, 시계 방향)를 16방위 · 8방위 이름으로, 두 점 사이 방위각 계산."""

from math import atan2, degrees

from shapely.geometry.base import BaseGeometry

COMPASS16 = (
    "북", "북북동", "북동", "동북동", "동", "동남동", "남동", "남남동",
    "남", "남남서", "남서", "서남서", "서", "서북서", "북서", "북북서",
)  # fmt: skip
COMPASS8 = ("북", "북동", "동", "남동", "남", "남서", "서", "북서")


def compass16(deg: float) -> str:
    """16방위(22.5°씩): 270° → 「서」."""
    return COMPASS16[round(deg / 22.5) % 16]


def compass8(deg: float) -> str:
    """8방위(45°씩): 90° → 「동」."""
    return COMPASS8[round(deg / 45) % 8]


def azimuth(a: BaseGeometry, b: BaseGeometry) -> float:
    """a에서 b로 가는 방위각(도, 북 = 0 · 시계 방향). EPSG:5179는 y가 북쪽."""
    return degrees(atan2(b.x - a.x, b.y - a.y)) % 360
