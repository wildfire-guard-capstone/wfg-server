from __future__ import annotations

import argparse

from common import (
    DEFAULT_CONFIG,
    extent_args,
    gdal_modules,
    load_context,
    resolve,
    run,
    write_array_like,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="ESA WorldCover를 FBFM40 연료모델로 변환합니다.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()
    cfg, root = load_context(args.config)
    inputs = root / "inputs"
    source = resolve(root, cfg["sources"]["worldcover"])
    worldcover = inputs / "worldcover.tif"
    res = str(cfg["resolution_m"])
    run(["gdalwarp", "-overwrite", "-t_srs", cfg["crs"], "-te", *extent_args(cfg),
         "-tr", res, res, "-r", "mode", "-srcnodata", "0", "-dstnodata", "0",
         "-ot", "Byte", "-co", "COMPRESS=DEFLATE", str(source), str(worldcover)])

    gdal, _ = gdal_modules()
    import numpy as np

    arr = gdal.Open(str(worldcover)).ReadAsArray()
    fuel = np.zeros(arr.shape, dtype=np.int16)
    for source_value, fuel_value in cfg["worldcover_to_fbfm40"].items():
        fuel[arr == int(source_value)] = int(fuel_value)
    unknown = sorted(int(value) for value in np.unique(arr[(arr != 0) & (fuel == 0)]))
    if unknown:
        raise RuntimeError(f"매핑되지 않은 WorldCover 코드: {unknown}")
    write_array_like(worldcover, inputs / "fbfm40_landcover.tif", fuel, gdal.GDT_Int16, -9999)
    print("토지피복 연료 입력 완료: fbfm40_landcover.tif")


if __name__ == "__main__":
    main()
