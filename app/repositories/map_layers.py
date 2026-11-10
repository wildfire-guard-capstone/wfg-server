"""지도 레이어(명세 7.1): 레이어 id → 테이블 · 속성, 범위(bbox) 안 도형을 GeoJSON으로 읽는다."""

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

OSM = "© OpenStreetMap contributors"


@dataclass(frozen=True)
class Layer:
    id: str
    name: str
    entity_type: str  # admin_area · village · facility · map_feature
    source: str
    reference_date: str | None = None
    attribution: str | None = None

    @property
    def kind(self) -> str | None:
        """map_features.kind(지도 자료 레이어만)."""
        return self.id if self.entity_type == "map_feature" else None


def _osm(id_: str, name: str) -> Layer:
    return Layer(id_, name, "map_feature", "OpenStreetMap(Geofabrik)", "2026-10-25", OSM)


LAYERS: dict[str, Layer] = {
    layer.id: layer
    for layer in [
        Layer(
            "admin_areas", "행정경계", "admin_area", "통계청 SGIS 경계(2025-2Q) · 행안부 법정동코드"
        ),
        Layer(
            "villages",
            "법정리",
            "village",
            "주소정보누리집 법정리 경계 · 행안부 리별 주민등록 인구",
        ),
        Layer(
            "facilities",
            "시설",
            "facility",
            "행안부 · 소방청 · 국가유산청 · 건보공단 · 심평원 · OSM(경찰 일부) — 시설 종류별",
            attribution=f"일부 {OSM}",
        ),
        _osm("road", "도로"),
        _osm("track", "임도(농로 섞임)"),
        _osm("power_line", "송전선"),
        _osm("power_tower", "송전 철탑"),
        _osm("substation", "변전소 · 발전소"),
        _osm("water", "담수지"),
        Layer("house", "주택", "map_feature", "국토교통부 GIS건물통합정보(단독 · 공동주택)"),
    ]
}

# 레이어 종류별: (테이블, 속성 칸 SQL). 도형은 공통으로 ST_AsGeoJSON(4326)
_COLUMNS = {
    "admin_area": (
        "admin_areas",
        "id, level::text AS level, code, name, parent_id, neighbor_ids",
    ),
    "village": (
        "villages",
        "id, code, admin_area_id, name, population, population_month, pop65_est, pop0_14_est,"
        " houses_est, ST_AsGeoJSON(ST_Transform(label_point, 4326), 6) AS label_point",
    ),
    "facility": (
        "facilities",
        "id, type::text AS type, subtype, name, capacity, capacity_unit, address, phone,"
        " village_id, sigungu_id, attrs",
    ),
    "map_feature": ("map_features", "id, kind, name, attrs"),
}
_ID_LISTS = {"neighbor_ids"}
_ID_FIELDS = {"id", "parent_id", "admin_area_id", "village_id", "sigungu_id"}


def available(session: Session, layer: Layer) -> bool:
    """실제로 적재돼 있나(명세 MAP-01)."""
    table, _ = _COLUMNS[layer.entity_type]
    where = "WHERE kind = :k" if layer.kind else ""
    return bool(
        session.scalar(text(f"SELECT EXISTS (SELECT 1 FROM {table} {where})"), {"k": layer.kind})
    )


def _props(row: dict[str, Any]) -> dict[str, Any]:
    """ID는 문자열로, 라벨 점은 GeoJSON으로, 날짜는 YYYY-MM-DD로."""
    out = {}
    for k, v in row.items():
        if k in _ID_FIELDS and v is not None:
            v = str(v)
        elif k in _ID_LISTS:
            v = [str(x) for x in v]
        elif k == "label_point" and v is not None:
            v = json.loads(v)
        elif k == "population_month" and v is not None:
            v = v.isoformat()
        out[k] = v
    return out


def features(
    session: Session,
    layer: Layer,
    bbox: tuple[float, float, float, float],
    after_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    """범위에 걸친 도형을 id 순으로 limit + 1개(Feature 모양)."""
    table, columns = _COLUMNS[layer.entity_type]
    sql = (
        f"SELECT {columns}, ST_AsGeoJSON(ST_Transform(geom, 4326), 6) AS __geom FROM {table}"
        " WHERE ST_Intersects(geom, ST_Transform(ST_MakeEnvelope(:x1, :y1, :x2, :y2, 4326), 5179))"
        " AND id > :after"
    )
    params = dict(
        zip(("x1", "y1", "x2", "y2"), bbox, strict=True), after=after_id or 0, n=limit + 1
    )
    if layer.kind:
        sql += " AND kind = :k"
        params["k"] = layer.kind
    rows = session.execute(text(sql + " ORDER BY id LIMIT :n"), params).mappings()
    out = []
    for r in rows:
        row = dict(r)
        geom = json.loads(row.pop("__geom"))
        props = _props(row)
        out.append({"type": "Feature", "id": props["id"], "geometry": geom, "properties": props})
    return out
