from __future__ import annotations

import argparse

from common import DEFAULT_CONFIG, extent_args, load_context, resolve, run


def main() -> None:
    parser = argparse.ArgumentParser(description="DEM을 자르고 경사·사면방향을 만듭니다.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()
    cfg, root = load_context(args.config)
    inputs = root / "inputs"
    inputs.mkdir(exist_ok=True)
    source = resolve(root, cfg["sources"]["dem"])
    dem = inputs / "dem.tif"
    res = str(cfg["resolution_m"])

    run(["gdalwarp", "-overwrite", "-t_srs", cfg["crs"], "-te", *extent_args(cfg),
         "-tr", res, res, "-r", "bilinear", "-dstnodata", "-9999", "-ot", "Float32",
         "-co", "COMPRESS=DEFLATE", str(source), str(dem)])
    run(["gdaldem", "slope", str(dem), str(inputs / "slp_float.tif"), "-compute_edges",
         "-co", "COMPRESS=DEFLATE"])
    run(["gdaldem", "aspect", str(dem), str(inputs / "asp_float.tif"), "-zero_for_flat",
         "-compute_edges", "-co", "COMPRESS=DEFLATE"])
    run(["gdal_translate", "-ot", "Int16", "-a_nodata", "-9999", "-co", "COMPRESS=DEFLATE",
         str(inputs / "slp_float.tif"), str(inputs / "slp.tif")])
    run(["gdal_translate", "-ot", "Int16", "-a_nodata", "-9999", "-co", "COMPRESS=DEFLATE",
         str(inputs / "asp_float.tif"), str(inputs / "asp.tif")])
    print("지형 입력 완료: dem.tif, slp.tif, asp.tif")


if __name__ == "__main__":
    main()
