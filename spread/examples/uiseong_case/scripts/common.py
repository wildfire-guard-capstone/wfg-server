from __future__ import annotations

import json
import subprocess
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR.parent / "pipeline_config.json"


def load_context(config_path: str | Path = DEFAULT_CONFIG):
    path = Path(config_path).resolve()
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return cfg, path.parent


def resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def run(command: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(str(item) for item in command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def gdal_modules():
    from osgeo import gdal, ogr

    gdal.UseExceptions()
    ogr.UseExceptions()
    return gdal, ogr


def grid_signature(path: Path):
    gdal, _ = gdal_modules()
    ds = gdal.Open(str(path))
    return ds.RasterXSize, ds.RasterYSize, ds.GetGeoTransform(), ds.GetProjection()


def assert_grid(path: Path, reference: Path) -> None:
    a = grid_signature(path)
    b = grid_signature(reference)
    if a[:2] != b[:2] or any(abs(x - y) > 1e-7 for x, y in zip(a[2], b[2], strict=True)):
        raise RuntimeError(f"격자가 일치하지 않습니다: {path} != {reference}")


def write_array_like(reference: Path, output: Path, array, data_type, nodata=-9999) -> None:
    gdal, _ = gdal_modules()
    import numpy as np

    ref = gdal.Open(str(reference))
    ensure_parent(output)
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(
        str(output), ref.RasterXSize, ref.RasterYSize, 1, data_type,
        options=["COMPRESS=DEFLATE", "TILED=YES"]
    )
    ds.SetGeoTransform(ref.GetGeoTransform())
    ds.SetProjection(ref.GetProjection())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    band.WriteArray(np.asarray(array))
    band.FlushCache()
    ds = None


def write_constant_bands(reference: Path, output: Path, values: list[float]) -> None:
    gdal, _ = gdal_modules()
    import numpy as np

    ref = gdal.Open(str(reference))
    ensure_parent(output)
    ds = gdal.GetDriverByName("GTiff").Create(
        str(output), ref.RasterXSize, ref.RasterYSize, len(values), gdal.GDT_Float32,
        options=["COMPRESS=DEFLATE", "TILED=YES"]
    )
    ds.SetGeoTransform(ref.GetGeoTransform())
    ds.SetProjection(ref.GetProjection())
    for index, value in enumerate(values, start=1):
        band = ds.GetRasterBand(index)
        band.SetNoDataValue(-9999)
        band.WriteArray(np.full((ref.RasterYSize, ref.RasterXSize), value, dtype=np.float32))
    ds = None


def build_landscape(reference: Path, output: Path, band_paths: list[Path]) -> None:
    gdal, _ = gdal_modules()
    import numpy as np

    ref = gdal.Open(str(reference))
    ensure_parent(output)
    ds = gdal.GetDriverByName("GTiff").Create(
        str(output), ref.RasterXSize, ref.RasterYSize, len(band_paths), gdal.GDT_Int16,
        options=["COMPRESS=DEFLATE", "TILED=YES"]
    )
    ds.SetGeoTransform(ref.GetGeoTransform())
    ds.SetProjection(ref.GetProjection())
    for index, source in enumerate(band_paths, start=1):
        assert_grid(source, reference)
        src = gdal.Open(str(source))
        arr = src.GetRasterBand(1).ReadAsArray().astype(np.int16)
        band = ds.GetRasterBand(index)
        band.SetNoDataValue(-9999)
        band.WriteArray(arr)
    ds = None


def extent_args(cfg: dict) -> list[str]:
    return [str(value) for value in cfg["extent"]]
