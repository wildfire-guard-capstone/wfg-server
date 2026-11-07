"""의성 데모 데이터 준비(한 번, 로컬): 원자료 → 데모 데이터 폴더(EPSG:5179).

    python -m demo.uiseong.prepare_data --map-zip <의성_지도데이터_20261106.zip>
        --sgis-zip <SGIS 경계 zip>
        --perims <perims.json> --out <데모 데이터 폴더>

만드는 것(공개 저장소에는 넣지 않는다):
  map/*.geojson       의성 지도 데이터(경위도, 값 그대로)
  regions.json        발화점 120 km 안 시 · 도, 시 · 군 · 구 경계(EPSG:5179, 20 m 단순화)
                      + 60 km 안 의성 밖 읍면동 경계 · 인구(townships, 리 자료 대신)
  perimeters.json     상황도 1 ~ 4보 화선(EPSG:5179, 백서 부록 2 좌표화 결과)
화선 좌표화는 `agent-spec/의성 시나리오(20261106)/_빌드/v2_공간계산`(11/6)의 결과를 그대로 쓴다.
"""

import argparse
import io
import json
import shutil
import zipfile
from pathlib import Path

import shapefile  # pyshp
from shapely.geometry import Point, mapping, shape

IGNITION_5179 = (1098492.2, 1819712.0)  # 안평면 괴산리 산61 (ELMFIRE 입력 11/1과 같은 점)
RADIUS_M = 120_000
REPORTS = {"1": "12:50", "2": "13:10", "3": "13:45", "4": "14:20"}


def _read_shp(z: zipfile.ZipFile, stem: str):
    names = {Path(n).suffix: n for n in z.namelist() if Path(n).stem == stem}
    r = shapefile.Reader(
        shp=io.BytesIO(z.read(names[".shp"])),
        shx=io.BytesIO(z.read(names[".shx"])),
        dbf=io.BytesIO(z.read(names[".dbf"])),
        encoding="utf-8",
    )
    return r


def regions(sgis_zip: Path) -> dict:
    ig = Point(IGNITION_5179)
    area = ig.buffer(RADIUS_M)
    out = {}
    with zipfile.ZipFile(sgis_zip) as z:
        z_names = [Path(n).stem for n in z.namelist() if n.endswith(".shp")]
        for level, prefix, field in (
            ("sido", "bnd_sido", "SIDO_NM"),
            ("sigungu", "bnd_sigungu", "SIGUNGU_NM"),
        ):
            stem = next(s for s in z_names if s.startswith(prefix))
            r = _read_shp(z, stem)
            fields = [f[0] for f in r.fields[1:]]
            rows = []
            for sr in r.iterShapeRecords():
                x0, y0, x1, y1 = sr.shape.bbox
                if (
                    x1 < area.bounds[0]
                    or x0 > area.bounds[2]
                    or y1 < area.bounds[1]
                    or y0 > area.bounds[3]
                ):
                    continue
                g = shape(sr.shape.__geo_interface__).buffer(0)
                if not g.intersects(area):
                    continue
                rec = dict(zip(fields, sr.record, strict=True))
                code = rec.get("SIGUNGU_CD") or rec.get("SIDO_CD")
                rows.append(
                    {
                        "name": rec[field],
                        "code": code,
                        "geom": mapping(g.simplify(20, preserve_topology=True)),
                    }
                )
            out[level] = rows
    # 시 · 군 · 구에 시 · 도 이름을 붙인다(코드 앞 2자리)
    sido_by_code = {s["code"]: s["name"] for s in out["sido"]}
    for s in out["sigungu"]:
        s["sido"] = sido_by_code.get(str(s["code"])[:2])
    return out


def townships(sgis_zip: Path, sigungu_rows: list[dict]) -> list[dict]:
    """의성 밖 읍면동(발화점 60 km 안): 리 자료가 없는 시군의 마을 대신 쓴다
    (11/7 사용자 결정, 데모 한정).

    경계 = SGIS 행정동 경계(2025-2Q), 인구 = SGIS 2024 인구총괄(총인구, to_in_001).
    """
    import csv

    area = Point(IGNITION_5179).buffer(60_000)
    sg_by_code = {str(s["code"]): s["name"] for s in sigungu_rows}
    home = next(str(s["code"]) for s in sigungu_rows if s["name"] == "의성군")
    with zipfile.ZipFile(sgis_zip) as z:
        pop_name = next(n for n in z.namelist() if n.endswith("인구총괄(총인구).csv"))
        pop = {}
        for row in csv.DictReader(io.StringIO(z.read(pop_name).decode("cp949"))):
            if row["통계항목"] == "to_in_001":
                pop[row["행정구역코드"]] = int(row["통계값"])
        stem = next(Path(n).stem for n in z.namelist() if n.endswith(".shp") and "bnd_dong" in n)
        r = _read_shp(z, stem)
        fields = [f[0] for f in r.fields[1:]]
        out = []
        for sr in r.iterShapeRecords():
            rec = dict(zip(fields, sr.record, strict=True))
            code = rec["ADM_CD"]
            if code[:5] == home or code[:5] not in sg_by_code:
                continue
            x0, y0, x1, y1 = sr.shape.bbox
            if (
                x1 < area.bounds[0]
                or x0 > area.bounds[2]
                or y1 < area.bounds[1]
                or y0 > area.bounds[3]
            ):
                continue
            g = shape(sr.shape.__geo_interface__).buffer(0)
            if not g.intersects(area):
                continue
            out.append(
                {
                    "code": code,
                    "name": rec["ADM_NM"],
                    "sigungu": sg_by_code[code[:5]],
                    "pop": pop.get(code),
                    "geom": mapping(g.simplify(20, preserve_topology=True)),
                }
            )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--map-zip", type=Path, required=True)
    ap.add_argument("--sgis-zip", type=Path, required=True)
    ap.add_argument("--perims", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    tmp = a.out / "_map_tmp"
    with zipfile.ZipFile(a.map_zip) as z:
        z.extractall(tmp)
    src = next(p for p in tmp.rglob("*.geojson")).parent
    if (a.out / "map").exists():
        shutil.rmtree(a.out / "map")
    shutil.move(str(src), a.out / "map")
    shutil.rmtree(tmp, ignore_errors=True)
    reg = regions(a.sgis_zip)
    reg["townships"] = townships(a.sgis_zip, reg["sigungu"])
    (a.out / "regions.json").write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    raw = json.loads(a.perims.read_text(encoding="utf-8"))
    per = {
        "crs": "EPSG:5179",
        "source": "백서 부록 2 상황도 좌표화(11/6, v2_공간계산 perims.json)",
        "ignition": {"x": IGNITION_5179[0], "y": IGNITION_5179[1], "at": "11:24"},
        "reports": [{"no": k, "at": t, "geom": raw[k]} for k, t in REPORTS.items()],
        "later": [
            {"no": k, "at": t, "geom": raw[k]}
            for k, t in {"5": "16:30", "6": "19:00", "7": "22:00"}.items()
        ],
    }
    (a.out / "perimeters.json").write_text(json.dumps(per, ensure_ascii=False), encoding="utf-8")
    print(
        f"시 · 도 {len(reg['sido'])} · 시 · 군 · 구 {len(reg['sigungu'])} · "
        f"의성 밖 읍면동 {len(reg['townships'])} · "
        f"화선 {len(per['reports'])}보 → {a.out}"
    )


if __name__ == "__main__":
    main()
