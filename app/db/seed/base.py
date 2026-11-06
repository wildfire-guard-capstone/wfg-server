"""바탕 데이터 적재 — DB의 바탕 데이터를 모두 지우고 적재 파일에서 다시 넣는다.

    python -m app.db.seed.base            # 대상 DB와 할 일만 보여 줌(아무것도 바꾸지 않음)
    python -m app.db.seed.base --yes      # 실제로 지우고 다시 넣음

대상 테이블: admin_areas · villages · facilities · map_features · resource_bases · resources.
이 테이블을 가리키는 시연 데이터(계정 · 산불 · 화선 · 예측 · 제안 …)도 함께 비워진다
(TRUNCATE … CASCADE). 시연 데이터는 따로 다시 넣는다.

적재 파일 폴더: --data-dir 또는 설정 BASE_DATA_DIR (capston-data `data/지도자원/적재`).
좌표는 파일에서 WGS84 경위도, DB에서는 EPSG:5179로 바꿔 넣는다.
"""

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import Connection, create_engine, text

from app.core.config import get_settings

BASE_TABLES = [
    "admin_areas",
    "villages",
    "facilities",
    "map_features",
    "resource_bases",
    "resources",
]

# GeoJSON (4326) → stored geometry (5179)
GEOM = "ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326), 5179)"
POINT = "ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 5179)"

# 인구 기준 달: 시연 산불(2025-03-22) 직전 달 — DB 설계 villages.population_month
POPULATION_MONTH = "2025-02-01"
POPULATION_KEY = "pop_2025_02"


def shelter_tier(subtype: str) -> str | None:
    """대피소 종류(규칙 엔진 R-09): 경로당 = 마을 시설, 그 밖 = 거점 시설.
    숙박시설(모텔)은 후보에서 빼므로 종류를 두지 않는다(11/4)."""
    if subtype == "경로당":
        return "village"
    if subtype == "숙박시설":
        return None
    return "hub"


def parse_coverage(text_: str) -> list[tuple[str | None, str]]:
    """「경상북도 안동시;대구광역시;군위군」 → [(시도, 이름), …]. 시도가 없으면 None."""
    out = []
    for part in filter(None, (p.strip() for p in text_.split(";"))):
        tokens = part.split(" ", 1)
        out.append((tokens[0], tokens[1]) if len(tokens) == 2 else (None, part))
    return out


def to_int(value: str) -> int | None:
    value = (value or "").strip()
    return int(float(value)) if value else None


def read_geojson(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_admin_areas(conn: Connection, data: Path) -> dict[str, int]:
    rows = read_geojson(data / "admin_areas.geojson")
    order = {"sido": 0, "sigungu": 1, "emd": 2}
    ids: dict[str, int] = {}
    for f in sorted(rows, key=lambda f: order[f["properties"]["level"]]):
        p = f["properties"]
        ids[p["bjd_cd"]] = conn.execute(
            text(
                f"INSERT INTO admin_areas (level, code, name, parent_id, geom) "
                f"VALUES (:level, :code, :name, :parent_id, ST_Multi({GEOM})) RETURNING id"
            ),
            {
                "level": {"sido": "SIDO", "sigungu": "SIGUNGU", "emd": "EMD"}[p["level"]],
                "code": p["bjd_cd"],
                "name": p["name"],
                "parent_id": ids.get(p.get("parent")),
                "geom": json.dumps(f["geometry"]),
            },
        ).scalar_one()
    # 인접 시군구: 경계를 20 m 단순화해 넣었으므로 맞닿음 대신 100 m 안이면 이웃으로 본다
    conn.execute(
        text(
            "UPDATE admin_areas a SET neighbor_ids = coalesce(("
            " SELECT array_agg(b.id ORDER BY b.id) FROM admin_areas b"
            " WHERE b.level = 'SIGUNGU' AND b.id <> a.id AND ST_DWithin(a.geom, b.geom, 100)"
            "), '{}') WHERE a.level = 'SIGUNGU'"
        )
    )
    return ids


def load_villages(conn: Connection, data: Path, area_ids: dict[str, int]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for f in read_geojson(data / "villages.geojson"):
        p = f["properties"]
        ids[p["bjd_cd"]] = conn.execute(
            text(
                "INSERT INTO villages (code, admin_area_id, name, geom, label_point, population,"
                " population_month, pop65_est, pop0_14_est, houses_est) VALUES (:code, :area,"
                f" :name, ST_Multi({GEOM}), {POINT}, :pop, :month, :pop65, :pop014, :houses)"
                " RETURNING id"
            ),
            {
                "code": p["bjd_cd"],
                "area": area_ids[p["emd_bjd_cd"]],
                "name": p["name"],
                "geom": json.dumps(f["geometry"]),
                "lon": p["label_lon"],
                "lat": p["label_lat"],
                "pop": p[POPULATION_KEY],
                "month": POPULATION_MONTH,
                "pop65": p.get("pop65_est"),
                "pop014": p.get("pop0_14_est"),
                "houses": p.get("houses_est"),
            },
        ).scalar_one()
    return ids


def load_facilities(conn: Connection, data: Path, village_ids: dict[str, int]) -> int:
    n = 0
    for r in read_csv(data / "facilities.csv"):
        attrs = json.loads(r["attrs"] or "{}")
        if r["type"] == "shelter":
            attrs["tier"] = shelter_tier(r["subtype"])
        conn.execute(
            text(
                "INSERT INTO facilities (type, subtype, name, capacity, capacity_unit, address,"
                " phone, village_id, sigungu_id, geom, attrs) VALUES (:type, :subtype, :name,"
                f" :capacity, :unit, :address, :phone, :village, ("
                # 위치가 든 시군구, 해안 등 단순화 경계 밖이면 가장 가까운 시군구
                " SELECT id FROM admin_areas WHERE level = 'SIGUNGU'"
                f" ORDER BY ST_Distance(geom, {POINT}) LIMIT 1),"
                f" {POINT}, CAST(:attrs AS jsonb))"
            ),
            {
                "type": r["type"].upper(),
                "subtype": r["subtype"],
                "name": r["name"],
                "capacity": to_int(r["capacity"]),
                "unit": r["capacity_unit"] or None,
                "address": r["address"] or None,
                "phone": r["phone"] or None,
                "village": village_ids.get(r["bjd_cd"]),
                "lon": float(r["lon"]),
                "lat": float(r["lat"]),
                "attrs": json.dumps(attrs, ensure_ascii=False),
            },
        )
        n += 1
    return n


def area_lookup(conn: Connection) -> tuple[dict, dict]:
    """(시도 이름, 시군구 이름) → id, 이름 하나 → id(시도 또는 이름이 하나뿐인 시군구)."""
    rows = conn.execute(
        text(
            "SELECT a.id, a.level, a.name, p.name FROM admin_areas a"
            " LEFT JOIN admin_areas p ON p.id = a.parent_id WHERE a.level <> 'EMD'"
        )
    ).all()
    pair = {(sido, name): id_ for id_, level, name, sido in rows if level == "SIGUNGU"}
    names = Counter(name for _, level, name, _ in rows if level == "SIGUNGU")
    single = {name: id_ for id_, level, name, _ in rows if level == "SIDO"}
    single |= {name: id_ for id_, level, name, _ in rows if level == "SIGUNGU" and names[name] == 1}
    return pair, single


def load_resources(conn: Connection, data: Path) -> tuple[int, int, list[str]]:
    pair, single = area_lookup(conn)
    unresolved: list[str] = []
    base_ids: dict[str, int] = {}
    for r in read_csv(data / "resource_bases.csv"):
        coverage = []
        for sido, name in parse_coverage(r["coverage"]):
            # 시도가 바뀐 곳(군위군: 2023 경북 → 대구)은 이름만으로 다시 찾는다
            id_ = pair.get((sido, name)) or single.get(name)
            if id_ is None:
                unresolved.append(f"{r['name']}: {sido or ''} {name}".strip())
            else:
                coverage.append(id_)
        has_point = bool(r["lon"] and r["lat"])
        base_ids[r["name"]] = conn.execute(
            text(
                "INSERT INTO resource_bases (name, org_type, geom, coverage_area_ids) VALUES"
                f" (:name, :org, {POINT if has_point else 'NULL'}, :coverage) RETURNING id"
            ),
            {
                "name": r["name"],
                "org": r["org_type"],
                "lon": float(r["lon"]) if has_point else None,
                "lat": float(r["lat"]) if has_point else None,
                "coverage": coverage,
            },
        ).scalar_one()
    n = 0
    for r in read_csv(data / "resources.csv"):
        conn.execute(
            text(
                "INSERT INTO resources (kind, name, base_id, status, attrs)"
                " VALUES (:kind, :name, :base, :status, CAST(:attrs AS jsonb))"
            ),
            {
                "kind": r["kind"],
                "name": r["name"],
                "base": base_ids[r["base"]],
                "status": r["status"] or "AVAILABLE",
                "attrs": r["attrs"] or "{}",
            },
        )
        n += 1
    return len(base_ids), n, unresolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data-dir", type=Path, default=get_settings().base_data_dir)
    parser.add_argument("--yes", action="store_true", help="실제로 지우고 다시 넣는다")
    args = parser.parse_args(argv)

    if args.data_dir is None or not args.data_dir.is_dir():
        print("적재 파일 폴더가 없습니다. --data-dir 또는 BASE_DATA_DIR을 지정하세요.")
        return 2

    engine = create_engine(get_settings().database_url)
    print(f"대상 DB: {engine.url.render_as_string(hide_password=True)}")
    print(f"적재 파일: {args.data_dir}")
    print(f"지우고 다시 넣을 테이블: {', '.join(BASE_TABLES)} (+ 이들을 가리키는 시연 데이터)")
    if not args.yes:
        print("확인만 했습니다. 실제로 하려면 --yes를 붙이세요.")
        return 0

    with engine.begin() as conn:  # 한 트랜잭션: 중간에 실패하면 아무것도 바뀌지 않는다
        conn.execute(text(f"TRUNCATE {', '.join(BASE_TABLES)} RESTART IDENTITY CASCADE"))
        area_ids = load_admin_areas(conn, args.data_dir)
        village_ids = load_villages(conn, args.data_dir, area_ids)
        n_fac = load_facilities(conn, args.data_dir, village_ids)
        n_bases, n_res, unresolved = load_resources(conn, args.data_dir)

        levels = dict(
            conn.execute(text("SELECT level, count(*) FROM admin_areas GROUP BY level")).all()
        )
        print(f"admin_areas: {levels}")
        print(f"villages: {len(village_ids)} · facilities: {n_fac}")
        print(f"resource_bases: {n_bases} · resources: {n_res}")
        if unresolved:
            print(f"관할 이름을 행정구역에서 못 찾음({len(unresolved)}): {unresolved}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
