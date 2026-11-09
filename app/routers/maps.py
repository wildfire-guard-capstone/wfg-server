"""MAP-01 지도 레이어 목록 · MAP-02 레이어 도형(범위 안, 페이지)."""

from typing import Any

from fastapi import APIRouter, Query, Request

from app.api.deps import Db, require_roles
from app.api.envelope import Envelope, ok
from app.api.errors import ApiError
from app.api.pagination import MAP_LIMIT, cut, decode_cursor
from app.repositories.map_layers import LAYERS, available, features
from app.schemas.map import LayerOut

router = APIRouter(prefix="/map", tags=["지도"])
Viewer = require_roles("COMMANDER", "VIEWER", "REPORTER")


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    """「min_lon,min_lat,max_lon,max_lat」(4326). 틀리면 422 INVALID_BBOX."""
    try:
        x1, y1, x2, y2 = (float(v) for v in value.split(","))
    except ValueError as exc:
        raise _bad_bbox("숫자 4개를 쉼표로: min_lon,min_lat,max_lon,max_lat") from exc
    if not (-180 <= x1 < x2 <= 180 and -90 <= y1 < y2 <= 90):
        raise _bad_bbox("경도 -180~180 · 위도 -90~90, 최소 < 최대여야 합니다.")
    return x1, y1, x2, y2


def _bad_bbox(reason: str) -> ApiError:
    return ApiError(
        422, "INVALID_BBOX", "지도 범위를 확인해주세요.", [{"field": "bbox", "reason": reason}]
    )


@router.get("/layers", response_model=Envelope[list[LayerOut]])
def list_layers(request: Request, user: Viewer, db: Db):
    """MAP-01. available = DB에 실제로 들어 있나."""
    return ok(request, [LayerOut.of(layer, available(db, layer)) for layer in LAYERS.values()])


@router.get("/layers/{layer_id}/features", response_model=Envelope[dict[str, Any]])
def layer_features(
    layer_id: str,
    request: Request,
    user: Viewer,
    db: Db,
    bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat (EPSG:4326)"),
    cursor: str | None = None,
    limit: int = MAP_LIMIT,
):
    """MAP-02. FeatureCollection + next_cursor · has_more. has_more가 거짓일 때까지 모은다."""
    layer = LAYERS.get(layer_id)
    if layer is None:
        raise ApiError(422, "UNKNOWN_LAYER", f"없는 레이어입니다: {layer_id}")
    box = parse_bbox(bbox)
    if not available(db, layer):
        raise ApiError(503, "LAYER_UNAVAILABLE", f"{layer.name} 레이어가 아직 적재되지 않았습니다.")
    after = decode_cursor(cursor, 1)
    rows = features(db, layer, box, int(after[0]) if after else None, limit)
    items, next_cursor, has_more = cut(rows, limit, lambda f: (int(f["id"]),))
    data = {
        "type": "FeatureCollection",
        "features": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }
    return ok(request, data)
