"""바탕 데이터 읽기(DB 설계 2.6) → 규칙 엔진 자료형(rules.core).

범위(area, EPSG:5179)에 걸친 것만 읽는다. 다만
- 시설은 범위에 걸친 시군구와 그 인접 시군구 전부(응급의료기관 · 관서는 이웃 시군 것도 씀)
- 진화자원 기지는 전부(동원 범위가 시 · 도 · 전국까지 넓음)
"""

import re
from collections import defaultdict
from typing import Any

from shapely import wkb
from shapely.geometry.base import BaseGeometry
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models._types import SRID
from rules.core.facility import Points
from rules.core.places import Base, Place, PowerLine, RoadPiece
from rules.core.regions import Region
from rules.core.villages import Village

IN_AREA = "ST_Intersects({col}, ST_GeomFromText(:wkt, :srid))"

# resource_bases.org_type → 엔진 기지 종류(rules Base.kind)
BASE_KIND = {
    "forest": "산림청 헬기",
    "local_lease": "지자체 임차헬기",
    "local": "지상 진화대",
}


def _area(area: BaseGeometry) -> dict[str, Any]:
    return {"wkt": area.wkt, "srid": SRID}


def _geom(value: bytes) -> BaseGeometry:
    return wkb.loads(bytes(value))


def regions(session: Session, area: BaseGeometry) -> tuple[list[Region], list[Region]]:
    """범위에 걸친 시 · 도와 시 · 군 · 구 경계."""
    rows = session.execute(
        text(
            "SELECT level, name, ST_AsBinary(geom) FROM admin_areas"
            f" WHERE level IN ('SIDO', 'SIGUNGU') AND {IN_AREA.format(col='geom')}"
        ),
        _area(area),
    ).all()
    sido = [Region(name, _geom(g)) for level, name, g in rows if level == "SIDO"]
    sigungu = [Region(name, _geom(g)) for level, name, g in rows if level == "SIGUNGU"]
    return sido, sigungu


def neighbors(session: Session, area: BaseGeometry) -> dict[str, list[str]]:
    """범위에 걸친 시 · 군 · 구 → 인접 시 · 군 · 구 이름(admin_areas.neighbor_ids)."""
    rows = session.execute(
        text(
            "SELECT a.name, b.name FROM admin_areas a"
            " JOIN admin_areas b ON b.id = ANY(a.neighbor_ids)"
            f" WHERE a.level = 'SIGUNGU' AND {IN_AREA.format(col='a.geom')} ORDER BY b.name"
        ),
        _area(area),
    ).all()
    out: dict[str, list[str]] = defaultdict(list)
    for name, neighbor in rows:
        out[name].append(neighbor)
    return dict(out)


def villages(session: Session, area: BaseGeometry) -> list[Village]:
    """범위에 걸친 리 — 읍면 · 시군구 이름 · 인구 · 65세 이상 추정(인근 리는 None)."""
    rows = session.execute(
        text(
            "SELECT v.id, v.name, e.name, s.name, ST_AsBinary(v.geom), v.population, v.pop65_est"
            " FROM villages v JOIN admin_areas e ON e.id = v.admin_area_id"
            " JOIN admin_areas s ON s.id = e.parent_id"
            f" WHERE {IN_AREA.format(col='v.geom')} ORDER BY v.id"
        ),
        _area(area),
    ).all()
    return [Village(i, n, emd, sgg, _geom(g), pop, p65) for i, n, emd, sgg, g, pop, p65 in rows]


def facilities(session: Session, area: BaseGeometry) -> dict[str, list[Place]]:
    """범위에 걸친 시군구 + 인접 시군구의 시설을 엔진 칸별로.

    칸: vulnerable · shelters · heritage · police · fire_stations · hospitals.
    hospitals = 응급실 있는 곳(attrs.emergency) — 병원 · 요양병원 등 종류와 상관없이(DB 설계 2.6).
    """
    rows = session.execute(
        text(
            "WITH near AS (SELECT id, neighbor_ids FROM admin_areas"
            f"   WHERE level = 'SIGUNGU' AND {IN_AREA.format(col='geom')})"
            " SELECT f.id, f.type, f.subtype, f.name, ST_AsBinary(f.geom), f.village_id, s.name,"
            " f.capacity, f.attrs FROM facilities f JOIN admin_areas s ON s.id = f.sigungu_id"
            " WHERE f.sigungu_id IN"
            "   (SELECT id FROM near UNION SELECT unnest(neighbor_ids) FROM near)"
            " ORDER BY f.id"
        ),
        _area(area),
    ).all()
    out: dict[str, list[Place]] = {
        k: []
        for k in ["vulnerable", "shelters", "heritage", "police", "fire_stations", "hospitals"]
    }
    slot = {
        "VULNERABLE": "vulnerable",
        "SHELTER": "shelters",
        "HERITAGE": "heritage",
        "POLICE": "police",
        "FIRE_STATION": "fire_stations",
    }
    for fid, ftype, subtype, name, g, village_id, sigungu, capacity, attrs in rows:
        place = Place(
            id=fid,
            name=name,
            kind=subtype,
            geom=_geom(g),
            village_id=village_id,
            sigungu=sigungu,
            daytime_only=bool(attrs.get("daytime_only")),
            emergency=bool(attrs.get("emergency")),
            capacity=capacity,
            tier=attrs.get("tier"),
        )
        if ftype in slot:
            out[slot[ftype]].append(place)
        if attrs.get("emergency"):
            out["hospitals"].append(place)
    return out


def junction_kind(name: str) -> str:
    """나들목(IC) / 분기점(JC)."""
    return "JC" if "분기점" in name or "JC" in name.upper() else "IC"


def voltage_kv(value: str | None) -> int | None:
    """OSM voltage(V, 「154000;22900」처럼 여러 개) → 가장 높은 kV."""
    numbers = [int(n) for n in re.findall(r"\d+", value or "")]
    return max(numbers) // 1000 if numbers else None


def substation_kind(attrs: dict[str, Any]) -> str:
    """변전소 / 발전소 종류(태양광은 엔진이 보호대상에서 뺌)."""
    if attrs.get("type") == "substation":
        return "변전소"
    return "태양광" if attrs.get("source") == "solar" else "발전소"


def map_features(session: Session, area: BaseGeometry, house_area: BaseGeometry) -> dict[str, Any]:
    """범위 안 도로 · 나들목 · 송전선 · 변전소, `house_area` 안 주택 점(2만여 개라 좁게)."""
    rows = session.execute(
        text(
            "SELECT id, kind, name, ST_AsBinary(geom), attrs FROM map_features"
            " WHERE kind IN ('road', 'junction', 'power_line', 'substation')"
            f" AND {IN_AREA.format(col='geom')} ORDER BY id"
        ),
        _area(area),
    ).all()
    roads, junctions, lines, subs = [], [], [], []
    for fid, kind, name, g, attrs in rows:
        geom = _geom(g)
        if kind == "road":
            roads.append(RoadPiece(fid, attrs.get("highway", ""), name, attrs.get("ref"), geom))
        elif kind == "junction" and name:
            junctions.append(Place(fid, name, junction_kind(name), geom))
        elif kind == "power_line" and (kv := voltage_kv(attrs.get("voltage"))):
            lines.append(PowerLine(fid, kv, geom))
        elif kind == "substation":
            subs.append(Place(fid, name or "", substation_kind(attrs), geom))
    xy = session.execute(
        text(
            "SELECT ST_X(geom), ST_Y(geom) FROM map_features"
            f" WHERE kind = 'house' AND {IN_AREA.format(col='geom')}"
        ),
        _area(house_area),
    ).all()
    houses: Points = ([x for x, _ in xy], [y for _, y in xy])
    return {
        "roads": roads,
        "junctions": junctions,
        "power_lines": lines,
        "substations": subs,
        "houses": houses,
    }


def bases(session: Session, incident_id: int) -> list[Base]:
    """진화자원 기지 전부 — 부를 수 있는(대기) 대수와 이 산불에 투입된 대수(자원 연결)."""
    rows = session.execute(
        text(
            "SELECT b.id, b.name, b.org_type, ST_AsBinary(b.geom), s.name, d.name,"
            " ARRAY(SELECT a.name FROM admin_areas a WHERE a.id = ANY(b.coverage_area_ids)),"
            " count(r.id) FILTER (WHERE r.kind = 'HELICOPTER'),"
            " count(r.id) FILTER (WHERE r.kind = 'HELICOPTER' AND r.status = 'AVAILABLE'),"
            " count(r.id) FILTER (WHERE r.kind = 'VEHICLE' AND r.status = 'AVAILABLE'),"
            " count(r.id) FILTER (WHERE r.kind = 'HELICOPTER' AND r.deployed_incident_id = :inc),"
            " count(r.id) FILTER (WHERE r.kind = 'VEHICLE' AND r.deployed_incident_id = :inc)"
            " FROM resource_bases b"
            " JOIN LATERAL (SELECT name, parent_id FROM admin_areas WHERE level = 'SIGUNGU'"
            "   ORDER BY ST_Distance(geom, b.geom) LIMIT 1) s ON true"
            " JOIN admin_areas d ON d.id = s.parent_id"
            " LEFT JOIN resources r ON r.base_id = b.id"
            " WHERE b.geom IS NOT NULL GROUP BY b.id, s.name, d.name ORDER BY b.id"
        ),
        {"inc": incident_id},
    ).all()
    out = []
    for bid, name, org, g, sgg, sido, coverage, heli, heli_av, veh_av, heli_dep, veh_dep in rows:
        kind = BASE_KIND.get(org) or ("소방 헬기" if org == "fire" and heli else "지상 소방")
        out.append(
            Base(
                id=bid,
                name=name,
                kind=kind,
                sigungu=sgg,
                sido=sido,
                coverage=list(coverage),
                geom=_geom(g),
                helicopters=heli_av,
                vehicles=veh_av,
                deployed_helicopters=heli_dep,
                deployed_vehicles=veh_dep,
            )
        )
    return out


def known_names(session: Session) -> set[str]:
    """바탕 데이터의 고유 이름(리 · 읍면동 · 시군구 · 시설 · 기지, 두 글자 이상) —
    에이전트 작성기가 제안 문장에 나온 이름이 판정 · 근거에 있는지 검사할 때 쓴다
    (의성 시연의 demo.uiseong.step3_agent.known_names와 같은 범위)."""
    rows = session.execute(
        text(
            "SELECT name FROM villages"
            " UNION SELECT name FROM admin_areas WHERE parent_id IS NOT NULL"
            " UNION SELECT name FROM facilities"
            " UNION SELECT name FROM resource_bases"
        )
    ).scalars()
    return {n for n in rows if n and len(n) >= 2}
