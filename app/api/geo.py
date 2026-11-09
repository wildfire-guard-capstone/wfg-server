"""GeoJSON(EPSG:4326, [경도, 위도]) ↔ DB 도형(EPSG:5179)."""

from typing import Any

from pyproj import Transformer
from shapely.errors import GEOSException
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from app.api.errors import ApiError

_TO_4326 = Transformer.from_crs(5179, 4326, always_xy=True).transform
_TO_5179 = Transformer.from_crs(4326, 5179, always_xy=True).transform
DIGITS = 6  # 약 0.1 m


def _round(coords: Any) -> Any:
    if isinstance(coords, (list, tuple)):
        if coords and isinstance(coords[0], (int, float)):
            return [round(c, DIGITS) for c in coords]
        return [_round(c) for c in coords]
    return coords


def to_geojson(geom: BaseGeometry) -> dict[str, Any]:
    """5179 도형 → 4326 GeoJSON geometry."""
    g = mapping(transform(_TO_4326, geom))
    return {"type": g["type"], "coordinates": _round(g["coordinates"])}


def from_geojson(value: dict[str, Any], kinds: tuple[str, ...], field: str) -> BaseGeometry:
    """4326 GeoJSON geometry → 5179 도형. 종류 · 좌표 범위 · 모양이 틀리면 422 INVALID_GEOMETRY."""

    def bad(reason: str) -> ApiError:
        return ApiError(
            422, "INVALID_GEOMETRY", "도형을 확인해주세요.", [{"field": field, "reason": reason}]
        )

    try:
        geom = shape(value)
    except (KeyError, TypeError, ValueError, AttributeError, GEOSException) as exc:
        raise bad("GeoJSON geometry가 아닙니다.") from exc
    if geom.geom_type not in kinds:
        raise bad(f"{' · '.join(kinds)}만 받습니다.")
    if geom.is_empty:
        raise bad("빈 도형입니다.")
    minx, miny, maxx, maxy = geom.bounds
    if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
        raise bad("좌표는 [경도, 위도](EPSG:4326) 순서여야 합니다.")
    out = transform(_TO_5179, geom)
    if not out.is_valid:
        raise bad("스스로 겹치는 등 올바르지 않은 도형입니다.")
    return out
