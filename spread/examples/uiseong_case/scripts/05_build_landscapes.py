from __future__ import annotations

import argparse

from common import DEFAULT_CONFIG, build_landscape, gdal_modules, load_context, write_array_like


def main() -> None:
    parser = argparse.ArgumentParser(description="ELMFIRE용 8밴드 landscape 파일을 만듭니다.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()
    _, root = load_context(args.config)
    inputs = root / "inputs"
    gdal, _ = gdal_modules()
    import numpy as np

    dem = inputs / "dem.tif"
    zero = inputs / "zero_int16.tif"
    shape = gdal.Open(str(dem)).ReadAsArray().shape
    write_array_like(dem, zero, np.zeros(shape, dtype=np.int16), gdal.GDT_Int16, -9999)
    baseline_fuel = inputs / "fbfm40.tif"
    write_array_like(
        dem, baseline_fuel, np.full(shape, 188, dtype=np.int16), gdal.GDT_Int16, -9999
    )
    write_array_like(
        dem, inputs / "adj.tif", np.ones(shape, dtype=np.float32), gdal.GDT_Float32, -9999
    )
    write_array_like(
        dem, inputs / "phi.tif", np.ones(shape, dtype=np.float32), gdal.GDT_Float32, -9999
    )
    base = [dem, inputs / "slp.tif", inputs / "asp.tif"]

    landscapes = {
        "landscape.tif": base + [baseline_fuel, zero, zero, zero, zero],
        "landscape_landcover.tif": base + [inputs / "fbfm40_landcover.tif", zero, zero, zero, zero],
        "landscape_forest_final.tif": base + [inputs / "fbfm40_forest.tif", zero, zero, zero, zero],
        "landscape_canopy_low.tif": base
        + [
            inputs / "fbfm40_forest.tif",
            inputs / "cc_forest_low.tif",
            inputs / "ch_forest.tif",
            zero,
            zero,
        ],
        "landscape_canopy.tif": base
        + [
            inputs / "fbfm40_forest.tif",
            inputs / "cc_forest.tif",
            inputs / "ch_forest.tif",
            zero,
            zero,
        ],
        "landscape_canopy_high.tif": base
        + [
            inputs / "fbfm40_forest.tif",
            inputs / "cc_forest_high.tif",
            inputs / "ch_forest.tif",
            zero,
            zero,
        ],
        "landscape_crown_korea.tif": base
        + [
            inputs / "fbfm40_forest.tif",
            inputs / "cc_forest.tif",
            inputs / "ch_forest.tif",
            inputs / "cbh_forest_korea.tif",
            inputs / "cbd_forest_korea.tif",
        ],
    }
    for filename, bands in landscapes.items():
        build_landscape(dem, inputs / filename, bands)
        print(f"완료: {filename}")


if __name__ == "__main__":
    main()
