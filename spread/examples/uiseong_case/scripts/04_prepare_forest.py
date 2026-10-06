from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZipFile

from common import (
    DEFAULT_CONFIG,
    extent_args,
    gdal_modules,
    load_context,
    resolve,
    run,
    write_array_like,
)


def remove_dataset(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-shm"), Path(str(path) + "-wal")):
        if candidate.exists():
            candidate.unlink()


def rasterize(gpkg: Path, layer: str, field: str, output: Path, cfg: dict) -> None:
    remove_dataset(output)
    res = str(cfg["resolution_m"])
    run(["gdal_rasterize", "-a", field, "-l", layer, "-te", *extent_args(cfg),
         "-tr", res, res, "-ot", "Int16", "-init", "0", "-a_nodata", "-9999",
         "-co", "COMPRESS=DEFLATE", str(gpkg), str(output)])


def main() -> None:
    parser = argparse.ArgumentParser(description="국내 임상도로 연료·수관 입력을 만듭니다.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()
    cfg, root = load_context(args.config)
    inputs = root / "inputs"
    source = resolve(root, cfg["sources"]["forest"])
    if not source.exists():
        archive = source.parent.parent / "47730.zip"
        if not archive.exists():
            raise FileNotFoundError(f"임상도 SHP 또는 ZIP이 없습니다: {source}, {archive}")
        source.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(archive) as handle:
            handle.extractall(source.parent)
        if not source.exists():
            raise FileNotFoundError(f"압축 해제 후 임상도 SHP를 찾지 못했습니다: {source}")
        print(f"임상도 압축 해제 완료: {archive.name}")
    clipped = inputs / "forest_case.gpkg"
    derived = inputs / "forest_derived.gpkg"
    remove_dataset(clipped)
    remove_dataset(derived)
    run(["ogr2ogr", "-f", "GPKG", "-nln", "forest", "-clipsrc", *extent_args(cfg),
         str(clipped), str(source)])

    sql = """
    SELECT *,
      CASE
        WHEN KOFTR_NM='제지' THEN 0
        WHEN KOFTR_NM='미립목지' THEN 102
        WHEN KOFTR_NM='관목덤불' THEN 142
        WHEN KOFTR_NM='낙엽송' THEN 183
        WHEN KOFTR_NM='침활혼효림' THEN 186
        WHEN FRTP_NM='활엽수림' AND DNST_NM='밀'
             AND CAST(SUBSTR(AGCLS_NM,1,1) AS INTEGER)>=4 THEN 189
        WHEN FRTP_NM='활엽수림' THEN 186
        WHEN KOFTR_NM IN ('소나무','리기다소나무','곰솔','잣나무') THEN 188
        ELSE 0 END AS fuel_model,
      CASE DNST_NM WHEN '소' THEN 40 WHEN '중' THEN 60 WHEN '밀' THEN 80 ELSE 0 END AS cc_mid,
      CASE DNST_NM WHEN '소' THEN 25 WHEN '중' THEN 51 WHEN '밀' THEN 71 ELSE 0 END AS cc_low,
      CASE DNST_NM WHEN '소' THEN 50 WHEN '중' THEN 70 WHEN '밀' THEN 90 ELSE 0 END AS cc_high,
      CAST(COALESCE(NULLIF(HEIGHT,''),'0') AS INTEGER)*10 AS ch_value,
      CASE WHEN KOFTR_NM IN ('소나무','침활혼효림','리기다소나무','낙엽송','곰솔','잣나무')
        THEN CAST(ROUND(CAST(COALESCE(NULLIF(HEIGHT,''),'0') AS REAL)*4.0) AS INTEGER)
        ELSE 0 END AS cbh_value,
      CASE KOFTR_NM WHEN '소나무' THEN 21 WHEN '침활혼효림' THEN 10
        WHEN '리기다소나무' THEN 22 WHEN '낙엽송' THEN 9 WHEN '곰솔' THEN 15
        WHEN '잣나무' THEN 34 ELSE 0 END AS cbd_value
    FROM forest
    """.strip()
    run(["ogr2ogr", "-f", "GPKG", "-nln", "forest_derived", "-dialect", "SQLite",
         "-sql", sql, str(derived), str(clipped)])

    targets = {
        "fuel_model": "fbfm40_forest_raw.tif",
        "cc_low": "cc_forest_low.tif",
        "cc_mid": "cc_forest.tif",
        "cc_high": "cc_forest_high.tif",
        "ch_value": "ch_forest.tif",
        "cbh_value": "cbh_forest_korea.tif",
        "cbd_value": "cbd_forest_korea.tif"
    }
    for field, filename in targets.items():
        rasterize(derived, "forest_derived", field, inputs / filename, cfg)

    gdal, _ = gdal_modules()
    import numpy as np

    raw = gdal.Open(str(inputs / "fbfm40_forest_raw.tif")).ReadAsArray()
    fallback = gdal.Open(str(inputs / "fbfm40_landcover.tif")).ReadAsArray()
    combined = np.where(raw > 0, raw, fallback).astype(np.int16)
    write_array_like(
        inputs / "dem.tif",
        inputs / "fbfm40_forest.tif",
        combined,
        gdal.GDT_Int16,
        -9999,
    )
    print("임상도 입력 완료: 산림 연료, 수관피복, 수고, 지하고, 수관밀도")


if __name__ == "__main__":
    main()
