"""Coordinate helpers: WGS84 lon/lat <-> UTM metres.

ELMFIRE works on a projected grid in metres. Korea spans UTM zones 51N–52N;
most of the mainland is 52N (EPSG:32652).
"""

from collections.abc import Callable

from pyproj import Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

WGS84 = 4326


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def projector(epsg: int, inverse: bool = False) -> Callable[..., tuple]:
    src, dst = (epsg, WGS84) if inverse else (WGS84, epsg)
    return Transformer.from_crs(src, dst, always_xy=True).transform


def to_utm(geom: BaseGeometry, epsg: int) -> BaseGeometry:
    return transform(projector(epsg), geom)
