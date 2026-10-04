"""Build an ELMFIRE input deck (rasters + elmfire.data) for one fire case.

Follows ELMFIRE tutorial 02 (transient wind), written in Python instead of shell:
  landscape.tif  8 Int16 bands: DEM, SLP, ASP, FBFM40, CC, CH, CBH, CBD
  adj.tif, phi.tif               Float32, 1 band
  ws/wd/m1/m10/m100/lh/lw.tif   Float32, one band per hour (band 1 = ignition time)
  elmfire.data                   Fortran namelist

Runs inside the ELMFIRE worker image (rasterio + GDAL CLI from its conda env).
rasterio is imported inside the raster-writing functions only, so the grid, weather
and namelist logic stays importable (and unit-tested) without the worker image.

Version v0 assumptions (pilot scenario, replaced later):
  - uniform fuel FBFM40 (default 188 TL8, 165 TU5 for comparison)
  - canopy zero (crown fire off, PD-07)
  - fixed fuel moisture, wind spatially uniform
  - station wind (AI Hub) used directly as ELMFIRE 20-ft wind  ⚠ height not corrected
"""

import csv
import json
import math
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from shapely.geometry import Point, shape

from spread.geo import to_utm, utm_epsg
from spread.units import ms_to_mph

NODATA = -9999


@dataclass
class DeckConfig:
    cell_m: float = 30.0
    domain_factor: float = 4.0  # domain side = factor x larger side of the perimeter bbox
    min_domain_m: float = 3000.0
    fbfm40: int = 188  # TL8 long-needle litter (v0 plan); compare with 165 TU5 via --fbfm40
    moisture_pct: dict[str, float] = field(
        default_factory=lambda: {"m1": 6, "m10": 8, "m100": 10, "lh": 30, "lw": 60}
    )
    adj: float = 1.0
    min_hours: float = 8.0  # always cover the P1–P8 horizon
    duration_margin: float = 1.5  # run past the observed duration so the area cut can be reached
    max_hours: float = 48.0
    extend_to_max: bool = False  # evaluation retry when the area cut was not reached


@dataclass
class Grid:
    epsg: int
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    cell: float

    @property
    def width(self) -> int:
        return round((self.xmax - self.xmin) / self.cell)

    @property
    def height(self) -> int:
        return round((self.ymax - self.ymin) / self.cell)

    @property
    def transform(self):
        from rasterio.transform import from_origin

        return from_origin(self.xmin, self.ymax, self.cell, self.cell)


@dataclass
class CaseFiles:
    """Case folder written by `evaluation.datasets.aihub extract`."""

    case_id: str
    ignition_lonlat: tuple[float, float]
    ignition_time: datetime
    end_time: datetime | None
    perimeter_wgs84: object  # shapely Polygon
    weather: list[dict]

    @classmethod
    def load(cls, case_dir: Path) -> "CaseFiles":
        meta = json.loads((case_dir / "meta.json").read_text())
        ign = json.loads((case_dir / "ignition.geojson").read_text())
        perim_path = case_dir / "perimeter_repaired.geojson"
        if not perim_path.exists():
            perim_path = case_dir / "perimeter.geojson"
        perim = shape(json.loads(perim_path.read_text())["geometry"])
        with (case_dir / "weather.csv").open() as f:
            rows = list(csv.DictReader(f))
        weather = [
            {
                "time": datetime.fromisoformat(r["time"]),
                "ws_ms": float(r["wind_speed_ms"]),
                "wd_deg": float(r["wind_dir_deg"]),
            }
            for r in rows
            if r["wind_speed_ms"] and r["wind_dir_deg"]
        ]
        end = meta.get("end")
        return cls(
            case_id=meta["case_id"],
            ignition_lonlat=tuple(ign["geometry"]["coordinates"]),
            ignition_time=datetime.fromisoformat(ign["properties"]["time"]),
            end_time=datetime.fromisoformat(end) if end else None,
            perimeter_wgs84=perim,
            weather=weather,
        )

    @property
    def duration_h(self) -> float | None:
        if self.end_time is None:
            return None
        return (self.end_time - self.ignition_time).total_seconds() / 3600


def make_grid(case: CaseFiles, cfg: DeckConfig) -> Grid:
    epsg = utm_epsg(*case.ignition_lonlat)
    perim = to_utm(case.perimeter_wgs84, epsg)
    ign = to_utm(Point(case.ignition_lonlat), epsg)
    minx, miny, maxx, maxy = perim.union(ign).bounds
    side = max(maxx - minx, maxy - miny) * cfg.domain_factor
    side = max(side, cfg.min_domain_m)
    half = math.ceil(side / 2 / cfg.cell_m) * cfg.cell_m  # whole cells each side -> edges aligned
    cx = math.floor((minx + maxx) / 2 / cfg.cell_m) * cfg.cell_m
    cy = math.floor((miny + maxy) / 2 / cfg.cell_m) * cfg.cell_m
    return Grid(epsg, cx - half, cy - half, cx + half, cy + half, cfg.cell_m)


def simulation_hours(case: CaseFiles, cfg: DeckConfig) -> float:
    if cfg.extend_to_max:
        return cfg.max_hours
    if case.duration_h is None:  # operational run: no observed end, just the P1–P8 horizon
        return cfg.min_hours
    return min(max(case.duration_h * cfg.duration_margin, cfg.min_hours), cfg.max_hours)


def hourly_weather(case: CaseFiles, n_hours: int) -> list[tuple[float, float]]:
    """(ws_mph, wd_deg) for hours 0..n_hours-1 after ignition.

    Each hour takes the latest observation at or before it (forward fill); hours
    before the first observation take the first one. Direction convention is the
    same in AI Hub and ELMFIRE (direction the wind blows from) — checked with
    tutorial 01: wd = 0 spreads the fire south.
    """
    obs = sorted(case.weather, key=lambda w: w["time"])
    if not obs:
        raise ValueError(f"{case.case_id}: no weather records")
    out = []
    for h in range(n_hours):
        t = case.ignition_time.timestamp() + h * 3600
        past = [w for w in obs if w["time"].timestamp() <= t]
        w = past[-1] if past else obs[0]
        out.append((ms_to_mph(w["ws_ms"]), w["wd_deg"]))
    return out


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _write(path: Path, grid: Grid, bands: np.ndarray, dtype: str) -> None:
    import rasterio

    if bands.ndim == 2:
        bands = bands[None]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=grid.width,
        height=grid.height,
        count=bands.shape[0],
        dtype=dtype,
        crs=f"EPSG:{grid.epsg}",
        transform=grid.transform,
        nodata=NODATA,
        compress="deflate",
    ) as dst:
        dst.write(bands.astype(dtype))


MAX_DEM_GAP = 0.01  # fraction of cells without DEM coverage before we refuse to run


def build_terrain(grid: Grid, dem_dir: Path, work: Path) -> tuple[np.ndarray, ...]:
    """DEM, slope (deg) and aspect (deg) on the grid, from Copernicus GLO-30 tiles.

    Cells outside the downloaded tiles would silently become elevation 0, so the warp
    marks them as nodata and a missing tile stops the run with the tile name to fetch.
    """
    import rasterio

    tiles = sorted(str(p) for p in dem_dir.glob("*.tif"))
    if not tiles:
        raise FileNotFoundError(f"no DEM tiles in {dem_dir}")
    vrt, dem, slp, asp = (work / n for n in ("dem.vrt", "dem.tif", "slp.tif", "asp.tif"))
    _run(["gdalbuildvrt", "-overwrite", str(vrt), *tiles])
    _run(
        [
            "gdalwarp", "-overwrite", "-t_srs", f"EPSG:{grid.epsg}", "-r", "bilinear",
            "-tr", str(grid.cell), str(grid.cell),
            "-te", str(grid.xmin), str(grid.ymin), str(grid.xmax), str(grid.ymax),
            "-ot", "Float32", "-dstnodata", str(NODATA), str(vrt), str(dem),
        ]
    )  # fmt: skip
    with rasterio.open(dem) as src:
        raw = src.read(1)
    gap = float(np.mean(~np.isfinite(raw) | (raw <= NODATA)))
    if gap > MAX_DEM_GAP:
        lon_lat = _corner_tiles(grid)
        raise FileNotFoundError(f"{gap:.0%} of the grid has no DEM — tiles needed: {lon_lat}")
    _run(["gdaldem", "slope", "-compute_edges", str(dem), str(slp)])
    _run(["gdaldem", "aspect", "-compute_edges", "-zero_for_flat", str(dem), str(asp)])
    arrays = []
    for p in (dem, slp, asp):
        with rasterio.open(p) as src:
            a = src.read(1)
            a = np.where(np.isfinite(a) & (a > NODATA), a, 0)
            arrays.append(np.rint(a))
    return tuple(arrays)


def _corner_tiles(grid: Grid) -> list[str]:
    """Copernicus tile names covering the grid corners (for the missing-tile message)."""
    from spread.geo import projector

    back = projector(grid.epsg, inverse=True)
    names = set()
    for x in (grid.xmin, grid.xmax):
        for y in (grid.ymin, grid.ymax):
            lon, lat = back(x, y)
            names.add(f"N{math.floor(lat):02d}_00_E{math.floor(lon):03d}_00")
    return sorted(names)


def build_deck(case: CaseFiles, run_dir: Path, dem_dir: Path, cfg: DeckConfig) -> dict:
    """Write all inputs for one case into run_dir/inputs and return run metadata."""
    if run_dir.exists():
        shutil.rmtree(run_dir)  # leftovers from an earlier run made ELMFIRE stall (10/4)
    inputs = run_dir / "inputs"
    scratch = run_dir / "scratch"
    for d in (inputs, scratch, run_dir / "outputs"):
        d.mkdir(parents=True, exist_ok=True)

    grid = make_grid(case, cfg)
    shp = (grid.height, grid.width)
    dem, slp, asp = build_terrain(grid, dem_dir, scratch)
    zeros = np.zeros(shp)
    landscape = np.stack([dem, slp, asp, np.full(shp, cfg.fbfm40), zeros, zeros, zeros, zeros])
    _write(inputs / "landscape.tif", grid, landscape, "int16")
    _write(inputs / "adj.tif", grid, np.full(shp, cfg.adj), "float32")
    _write(inputs / "phi.tif", grid, np.ones(shp), "float32")

    hours = simulation_hours(case, cfg)
    n_wx = math.ceil(hours) + 1
    wx = hourly_weather(case, n_wx)
    series = {
        "ws": [w[0] for w in wx],
        "wd": [w[1] for w in wx],
        **{k: [v] * n_wx for k, v in cfg.moisture_pct.items()},
    }
    for name, values in series.items():
        bands = np.stack([np.full(shp, v) for v in values])
        _write(inputs / f"{name}.tif", grid, bands, "float32")

    ign = to_utm(Point(case.ignition_lonlat), grid.epsg)
    tstop = round(hours * 3600)
    (inputs / "elmfire.data").write_text(
        NAMELIST.format(n_wx=n_wx, x_ign=ign.x, y_ign=ign.y, tstop=float(tstop))
    )
    meta = {
        "case_id": case.case_id,
        "epsg": grid.epsg,
        "bounds": [grid.xmin, grid.ymin, grid.xmax, grid.ymax],
        "cell_m": grid.cell,
        "shape": list(shp),
        "ignition_utm": [ign.x, ign.y],
        "tstop_s": tstop,
        "weather_hours": n_wx,
        "ws_mph": series["ws"],
        "wd_deg": series["wd"],
        "fbfm40": cfg.fbfm40,
        "moisture_pct": cfg.moisture_pct,
    }
    (run_dir / "deck.json").write_text(json.dumps(meta, indent=2))
    return meta


# Tutorial 02 elmfire.data, with ignition, stop time and weather band count filled in.
# SIMULATION_DT is 10 s instead of the tutorial's 1 s: ELMFIRE stops a run as "FIRE FRONT
# PROPAGATION STALLED" when phi changes by < 0.001 in one step (elmfire_level_set.f90), which
# a slow early fire (light wind, timber fuels) does at 1 s — OC20250323 stalled at 0.2 acre
# with 1 s and spread normally with 10 s or 30 s (10/4 test).
NAMELIST = """&INPUTS
FUELS_AND_TOPOGRAPHY_DIRECTORY = './inputs'
LANDSCAPE_FILENAME             = 'landscape'
ADJ_FILENAME                   = 'adj'
PHI_FILENAME                   = 'phi'
DT_METEOROLOGY                 = 3600.0
WEATHER_DIRECTORY              = './inputs'
WS_FILENAME                    = 'ws'
WD_FILENAME                    = 'wd'
M1_FILENAME                    = 'm1'
M10_FILENAME                   = 'm10'
M100_FILENAME                  = 'm100'
USE_CONSTANT_LH                = .FALSE.
MLH_FILENAME                   = 'lh'
USE_CONSTANT_LW                = .FALSE.
MLW_FILENAME                   = 'lw'
/

&OUTPUTS
OUTPUTS_DIRECTORY    = './outputs'
DTDUMP               = {tstop}
DUMP_FLIN            = .FALSE.
DUMP_SPREAD_RATE     = .FALSE.
DUMP_TIME_OF_ARRIVAL = .TRUE.
CONVERT_TO_GEOTIFF   = .FALSE.
/

&TIME_CONTROL
SIMULATION_DT    = 10.0
TARGET_CFL       = 0.2
SIMULATION_TSTOP = {tstop}
/

&MONTE_CARLO
NUM_METEOROLOGY_TIMES = {n_wx}
/

&SIMULATOR
NUM_IGNITIONS = 1
X_IGN(1)      = {x_ign}
Y_IGN(1)      = {y_ign}
T_IGN(1)      = 0.0
WX_BILINEAR_INTERPOLATION = .FALSE.
WSMFEFF_LOW_MULT = 0.011364
/

&MISCELLANEOUS
PATH_TO_GDAL                   = 'auto'
SCRATCH                        = './scratch'
/
"""
