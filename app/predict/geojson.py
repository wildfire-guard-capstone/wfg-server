"""예측 범위(P1 ~ P8, EPSG:5179 면) → GeoJSON FeatureCollection(EPSG:4326, [경도, 위도]).

API 명세의 공통 규칙(GeoJSON · EPSG:4326 · [경도, 위도] 순서)과 PRED-03의 properties
(hour · at · area_ha)를 따른다. 같은 모양이라 파일 예측(app.predict.file)이 그대로
다시 읽을 수 있다.
DB를 거치지 않으므로 GPU 서버에서 ELMFIRE만 돌려 결과를 넘길 때도 쓴다.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from pyproj import Transformer
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

WGS84 = 4326
# 소수 7자리 ≈ 1 cm. 6자리(≈ 10 cm)는 반올림 때문에 P(n)이 P(n−1)을 수 m²씩 벗어난다
# (의성 1보 출발 시험: 6자리 최대 12 m², 7자리 1 m², 파일 크기 +8 %)
DIGITS = 7


def _round(coords: Any, digits: int) -> Any:
    if isinstance(coords, float | int):
        return round(float(coords), digits)
    return [_round(c, digits) for c in coords]


def to_wgs84(geom: BaseGeometry, src_epsg: int = 5179) -> BaseGeometry:
    if src_epsg == WGS84:
        return geom
    return transform(Transformer.from_crs(src_epsg, WGS84, always_xy=True).transform, geom)


def feature(geom: BaseGeometry, src_epsg: int = 5179, digits: int = DIGITS, **props) -> dict:
    """면 하나 → Feature. 좌표는 [경도, 위도], MultiPolygon으로 통일(명세 PRED-03)."""
    g = mapping(to_wgs84(geom, src_epsg))
    if g["type"] == "Polygon":
        g = {"type": "MultiPolygon", "coordinates": [g["coordinates"]]}
    g = {"type": g["type"], "coordinates": _round(g["coordinates"], digits)}
    return {"type": "Feature", "geometry": g, "properties": props}


def slices_geojson(
    slices: Sequence[BaseGeometry],
    base_at: datetime,
    src_epsg: int = 5179,
    digits: int = DIGITS,
) -> dict[str, Any]:
    """P1 ~ P8 → FeatureCollection. properties = hour · at(base_at + hour시간) · area_ha.

    면적은 원래 좌표계(미터)에서 잰다 — 위경도로 바꾼 뒤에는 잴 수 없다.
    """
    features = []
    for h, geom in enumerate(slices, 1):
        area_ha = geom.area / 10_000 if src_epsg != WGS84 else None
        features.append(
            feature(
                geom,
                src_epsg,
                digits,
                hour=h,
                at=(base_at + timedelta(hours=h)).isoformat(),
                area_ha=None if area_ha is None else round(area_ha, 2),
            )
        )
    return {"type": "FeatureCollection", "features": features}
