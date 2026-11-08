"""변하지 않는 값 13개(지형 7 · 연료 6)의 지역 격자: 지역마다 한 번 미리 계산해 파일로 둔다.

위험도-정리.md 「변하지 않는 값」: 지역마다 한 번 계산해 등급으로 저장하고,
위험도를 계산할 때는 P5와 겹치기만 한다.
DB 설계 원칙(래스터는 파일, DB에는 경로)에 따라 GeoTIFF 두 개 + meta.json으로 둔다.

    <폴더>/grades.tif   13밴드 uint8 등급(1 ~ 5, 0 = 값 없음). 밴드 이름 = 변수 키
    <폴더>/values.tif   13밴드 float32 원래 값(화면 설명용, NaN = 값 없음)
    <폴더>/meta.json    격자 · 밴드 순서 · 계산 기준 · 원자료
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from risk import fuel as fuel_mod
from risk import terrain
from risk.criteria import STATIC_KEYS, aspect_grade, grade
from risk.overlap import Grid


@dataclass
class StaticGrid:
    grid: Grid
    values: dict[str, np.ndarray]
    grades: dict[str, np.ndarray]
    meta: dict = field(default_factory=dict)


def build_static_grid(
    dem: np.ndarray,
    fbfm: np.ndarray,
    cover_pct: np.ndarray,
    height_m: np.ndarray,
    grid: Grid,
    models: dict[int, "fuel_mod.FuelModel"],
    water: np.ndarray | None = None,
    meta: dict | None = None,
) -> StaticGrid:
    """DEM · 연료 모델 번호 · 수관피복률(%) · 수관높이(m) 격자(같은 격자) → 13개 변수 값과 등급."""
    cs = grid.cs
    slope, aspect, flat = terrain.slope_aspect(dem, cs)
    water_km, streams = terrain.water_distance_km(dem, cs, water)
    fl = fuel_mod.fuel_layers(fbfm, models)
    cover = np.where(np.isnan(fl["fuel_type"]), np.nan, cover_pct.astype("float64"))
    height = np.where(np.isnan(fl["fuel_type"]), np.nan, height_m.astype("float64"))
    values = {
        "elevation": dem.astype("float64"),
        "slope": slope,
        "aspect": np.where(flat, np.nan, aspect),
        "curvature": terrain.curvature(dem, cs),
        "tpi": terrain.tpi(dem, cs),
        "water_dist": water_km,
        "complexity": terrain.relief(dem),
        "fuel_type": fl["fuel_type"],
        "fuel_load": fl["fuel_load"],
        "fuel_depth": fl["fuel_depth"],
        "stand_age": fl["stand_age"],
        "canopy_height": height,
        "canopy_cover": cover,
    }
    grades = {}
    for k in STATIC_KEYS:
        if k == "aspect":
            grades[k] = aspect_grade(aspect, flat)
        elif k == "fuel_type":
            grades[k] = fuel_mod.fuel_type_grade(fl["fuel_type"])
        elif k == "canopy_height":
            grades[k] = fuel_mod.canopy_height_grade(height, cover)
        else:
            grades[k] = grade(k, values[k])
    info = {
        "stream_cells": int(streams.sum()),
        "flat_cells": int(flat.sum()),
        "parameters": {
            "flat_slope_deg": terrain.FLAT_SLOPE_DEG,
            "tpi_radius_m": terrain.TPI_RADIUS_M,
            "relief_window_cells": terrain.RELIEF_WINDOW,
            "stream_km2": terrain.STREAM_KM2,
        },
    }
    return StaticGrid(grid, values, grades, {**info, **(meta or {})})


def save(sg: StaticGrid, folder: Path) -> Path:
    import rasterio
    from rasterio.transform import from_origin

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    g = sg.grid
    prof = dict(
        driver="GTiff",
        height=g.h,
        width=g.w,
        count=len(STATIC_KEYS),
        crs="EPSG:5179",
        transform=from_origin(g.x0, g.y0, g.cs, g.cs),
        compress="deflate",
    )
    with rasterio.open(folder / "grades.tif", "w", **prof, dtype="uint8", nodata=0) as o:
        for i, k in enumerate(STATIC_KEYS, 1):
            a = sg.grades[k]
            o.write(np.where(np.isnan(a), 0, np.round(a)).astype("uint8"), i)
            o.set_band_description(i, k)
    with rasterio.open(folder / "values.tif", "w", **prof, dtype="float32", nodata=np.nan) as o:
        for i, k in enumerate(STATIC_KEYS, 1):
            o.write(sg.values[k].astype("float32"), i)
            o.set_band_description(i, k)
    meta = {
        "crs": "EPSG:5179",
        "grid": {"x0": g.x0, "y0": g.y0, "cs": g.cs, "h": g.h, "w": g.w},
        "bands": list(STATIC_KEYS),
        **sg.meta,
    }
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
    return folder


def load(folder: Path) -> StaticGrid:
    import rasterio

    folder = Path(folder)
    meta = json.loads((folder / "meta.json").read_text("utf-8"))
    g = meta["grid"]
    grid = Grid(g["x0"], g["y0"], g["cs"], g["h"], g["w"])
    grades, values = {}, {}
    with rasterio.open(folder / "grades.tif") as r:
        for i, k in enumerate(meta["bands"], 1):
            a = r.read(i).astype("float64")
            a[a == 0] = np.nan
            grades[k] = a
    with rasterio.open(folder / "values.tif") as r:
        for i, k in enumerate(meta["bands"], 1):
            values[k] = r.read(i).astype("float64")
    return StaticGrid(grid, values, grades, meta)
