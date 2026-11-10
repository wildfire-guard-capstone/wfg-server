"""MAP-01 지도 레이어 목록 · MAP-02 레이어 도형(범위 안, 페이지) · MAP-03 지도 대상 상세 ·
MAP-04 행정구역 검색."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Request

from app.api.deps import AnyUser, Db, require_roles
from app.api.envelope import Envelope, Page, ok
from app.api.errors import ApiError, not_found
from app.api.pagination import LIMIT, MAP_LIMIT, cut, decode_cursor
from app.db.models import ResourceBase
from app.db.models.enums import UserRole
from app.repositories import map_layers
from app.repositories.map_layers import LAYERS, available, features
from app.routers.resources import base_out
from app.schemas.map import LayerOut

router = APIRouter(prefix="/map", tags=["지도"])
areas_router = APIRouter(tags=["지도"])  # MAP-04는 /map 아래가 아니다(/admin-areas)
ENTITY_TYPES = ("village", "facility", "map_feature", "resource_base", "admin_area")
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


@router.get("/entities/{entity_type}/{entity_id}", response_model=Envelope[dict[str, Any]])
def entity_detail(
    entity_type: str,
    entity_id: Annotated[int, Path(gt=0)],
    request: Request,
    user: Viewer,
    db: Db,
):
    """MAP-03. 지도에서 고른 대상 하나: 마을 · 시설 · 지도 요소 · 기지 · 행정구역.

    기지는 통합지휘권자 · 열람자만(기지 목록 BASE-01과 같은 범위, 보고자 범위는 명세 Q-08).
    산불 문맥이 없어 기지의 거리 · 관할 구분은 null이다."""
    if entity_type not in ENTITY_TYPES:
        raise ApiError(
            422,
            "UNKNOWN_ENTITY_TYPE",
            f"없는 대상 종류입니다: {entity_type}",
            [{"field": "entity_type", "reason": " · ".join(ENTITY_TYPES)}],
        )
    if entity_type == "resource_base":
        if user.role is UserRole.REPORTER:
            raise ApiError(403, "FORBIDDEN_ROLE", "이 기능을 쓸 권한이 없습니다.")
        base = db.get(ResourceBase, entity_id)
        entity = base_out(base).model_dump(mode="json") if base is not None else None
    else:
        entity = map_layers.entity(db, entity_type, entity_id)
    if entity is None:
        raise not_found("지도 대상을 찾을 수 없습니다.")
    return ok(request, {"entity_type": entity_type, "entity": entity})


@areas_router.get("/admin-areas", response_model=Envelope[Page[dict[str, Any]]])
def search_admin_areas(
    request: Request,
    user: AnyUser,
    db: Db,
    level: Literal["SIDO", "SIGUNGU", "EMD"] | None = None,
    parent_id: int | None = Query(None, gt=0),
    q: str | None = Query(None, min_length=1, max_length=50, description="이름 검색"),
    cursor: str | None = None,
    limit: int = LIMIT,
):
    """MAP-04. id 순. 법정동코드는 문자열 그대로.

    관할 계정은 자기 시군구 · 그 위 시도 · 그 아래 읍면동만, 전산 관리자는 전국
    (계정 · 기지의 관할을 고를 때 쓴다)."""
    home = None if user.role is UserRole.ADMIN else user.admin_area_id
    after = decode_cursor(cursor, 1)
    rows = map_layers.admin_areas(
        db,
        level=level,
        parent_id=parent_id,
        q=q,
        home=home,
        after_id=int(after[0]) if after else None,
        limit=limit,
    )
    items, next_cursor, has_more = cut(rows, limit, lambda r: (int(r["id"]),))
    return ok(request, Page(items=items, next_cursor=next_cursor, has_more=has_more))
