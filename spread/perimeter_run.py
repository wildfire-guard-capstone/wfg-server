"""Run ELMFIRE on a prepared deck from an observed perimeter (or a point) and cut P0..P8.

A *deck* is a folder like the 2025 Uiseong input (11/1): `elmfire.data` + `inputs/*.tif`
(terrain, fuels, hourly weather bands). Time in the deck is seconds after weather band 1.

One run folder:
  inputs/*.tif     symlinks to the deck rasters, except
    phi.tif        start perimeter: -1 inside, +1 outside (elmfire_level_set.f90: phi <= 0 starts
                   burned with time of arrival = SIMULATION_TSTART)
    adj.tif        deck spread-rate adjustment x `adj` (calibration multiplier)
  elmfire.data     deck namelist with the time window, outputs and ignitions replaced
  outputs/         time_of_arrival_*.tif (seconds after band 1; 0 / negative = not burned)

Ranges follow the service definition: P0 = start perimeter, Pn = P0 + cells burned by t0 + n h.
Holes are filled (a range is an envelope, like 산불영향구역),
so P0 <= P1 <= ... <= P8 by construction.

Runs where ELMFIRE is installed (worker / GPU server): rasterio is imported inside functions.
"""

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from shapely.geometry import Point, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

HOUR_S = 3600.0
OWN_RASTERS = {"phi.tif", "adj.tif"}


@dataclass
class RunResult:
    run_dir: Path
    t_start_s: float
    t_stop_s: float
    adj: float
    wall_s: float
    toa: np.ndarray  # seconds after band 1, NaN = not burned
    start_mask: np.ndarray
    transform: object
    cell_ha: float
    log_tail: str = ""

    def burned_by(self, t_s: float) -> np.ndarray:
        return self.start_mask | (np.isfinite(self.toa) & (self.toa <= t_s + 1e-3))

    def area_ha(self, t_s: float) -> float:
        return float(fill_holes_mask(self.burned_by(t_s)).sum() * self.cell_ha)


@dataclass
class Deck:
    folder: Path
    elmfire: str  # path to the ELMFIRE executable
    env: dict = field(
        default_factory=dict
    )  # PATH / PROJ_DATA / GDAL_DATA for the GDAL tools ELMFIRE calls

    @property
    def namelist(self) -> str:
        return (self.folder / "elmfire.data").read_text(encoding="utf-8")

    def grid(self):
        import rasterio

        with rasterio.open(self.folder / "inputs" / "adj.tif") as r:
            return r.profile.copy(), r.read(1).astype("float32")


# ── namelist ─────────────────────────────────────────────────────────────


def _set(text: str, key: str, value: str) -> str:
    """Replace `KEY = …` (keeps a trailing comment out). Raises if the key is missing."""
    pat = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)([^!\n]*)(.*)$", re.M)
    if not pat.search(text):
        raise KeyError(key)
    return pat.sub(lambda m: f"{m.group(1)}{value}", text, count=1)


def edit_namelist(
    text: str,
    t_start: float,
    t_stop: float,
    ignition: tuple[float, float] | None,
    extra: tuple[tuple[float, float, float], ...] = (),
) -> str:
    """Deck namelist → one run: own folders, time window, one point ignition or none (phi start).

    extra = (x, y, t) point ignitions added after the start one (e.g. a second fire that
    starts later). Each burns at max(t, t_start + 60 s).
    """
    for key in (
        "FUELS_AND_TOPOGRAPHY_DIRECTORY",
        "WEATHER_DIRECTORY",
        "MISCELLANEOUS_INPUTS_DIRECTORY",
    ):
        text = _set(text, key, "'./inputs'")
    text = _set(text, "OUTPUTS_DIRECTORY", "'./outputs'")
    text = _set(text, "SCRATCH", "'./scratch'")
    text = _set(text, "SIMULATION_TSTART", f"{t_start:.1f}")
    text = _set(text, "SIMULATION_TSTOP", f"{t_stop:.1f}")
    for key in ("DUMP_SPREAD_RATE", "DUMP_FLIN", "DUMP_CROWN_FIRE"):  # only arrival time is used
        try:
            text = _set(text, key, ".FALSE.")
        except KeyError:
            pass
    text = re.sub(r"^\s*[XYT]_IGN\(\d+\).*\n", "", text, flags=re.M)
    pts = [] if ignition is None else [(*ignition, t_start)]
    pts += [(x, y, max(t, t_start + 60.0)) for x, y, t in extra]
    block = f"NUM_IGNITIONS           = {len(pts)}"
    for i, (x, y, t) in enumerate(pts, 1):
        block += (
            f"\nX_IGN({i})                = {x:.1f}\nY_IGN({i})                = {y:.1f}"
            f"\nT_IGN({i})                = {t:.1f}"
        )
    return re.sub(r"^\s*NUM_IGNITIONS\s*=.*$", block, text, count=1, flags=re.M)


# ── rasters ──────────────────────────────────────────────────────────────


def rasterize(geom: BaseGeometry, profile: dict) -> np.ndarray:
    """Cells whose centre is inside geom.

    A start smaller than one cell still burns its centre cell.
    """
    from rasterio.features import rasterize as _rz

    shp = (profile["height"], profile["width"])
    m = _rz([(geom, 1)], out_shape=shp, transform=profile["transform"], fill=0, dtype="uint8")
    m = m.astype(bool)
    if not m.any():
        c = geom.centroid
        col, row = ~profile["transform"] * (c.x, c.y)
        r, k = int(row), int(col)
        if 0 <= r < shp[0] and 0 <= k < shp[1]:
            m[r, k] = True
    return m


def fill_holes_mask(mask: np.ndarray) -> np.ndarray:
    from scipy import ndimage

    return ndimage.binary_fill_holes(mask)


def polygonize(mask: np.ndarray, transform, simplify_m: float = 10.0) -> BaseGeometry:
    """Burned cells → one (multi)polygon without holes, EPSG of the deck (5179 for Uiseong)."""
    from rasterio.features import shapes

    filled = fill_holes_mask(mask).astype("uint8")
    polys = [
        Polygon(shape(g).exterior)
        for g, v in shapes(filled, mask=filled.astype(bool), transform=transform)
        if v == 1
    ]
    if not polys:
        return Polygon()
    out = unary_union(polys)
    return out.simplify(simplify_m, preserve_topology=True) if simplify_m else out


# ── run ──────────────────────────────────────────────────────────────────


def run(
    deck: Deck,
    run_dir: Path,
    t_start_s: float,
    hours: float,
    start: BaseGeometry,
    adj: float = 1.0,
    extra_ignitions: tuple[tuple[float, float, float], ...] = (),
    own_inputs: dict[str, tuple[np.ndarray, dict]] | None = None,
    namelist: dict[str, str] | None = None,
    timeout_s: float | None = None,
) -> RunResult:
    """One ELMFIRE run from `start` (Polygon = perimeter, Point = ignition) for `hours`.

    extra_ignitions = (x, y, t) point ignitions besides the start (see edit_namelist).
    own_inputs = {name: (bands, rasterio profile)} written as inputs/<name>.tif for this run
      only (e.g. the request's weather instead of the deck's), never into the deck.
    namelist = {KEY: value} set after the time window (e.g. WS_FILENAME, HOUR_OF_YEAR).
    timeout_s = stop ELMFIRE after this many seconds (TimeoutError); None = no limit.
    """
    import rasterio

    own_inputs = own_inputs or {}
    own = OWN_RASTERS | {f"{name}.tif" for name in own_inputs}
    run_dir = Path(run_dir)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / "outputs").mkdir()
    (run_dir / "scratch").mkdir()
    for f in (deck.folder / "inputs").iterdir():
        if f.name not in own:
            os.symlink(f.resolve(), run_dir / "inputs" / f.name)
    for name, (bands, prof) in own_inputs.items():
        bands = np.asarray(bands, "float32")
        bands = bands[None] if bands.ndim == 2 else bands
        prof = {**prof, "count": bands.shape[0], "dtype": "float32", "nodata": None}
        with rasterio.open(run_dir / "inputs" / f"{name}.tif", "w", **prof) as w:
            w.write(bands)
    profile, base_adj = deck.grid()
    profile.update(dtype="float32", count=1, compress="deflate", nodata=None)
    is_point = isinstance(start, Point)
    start_mask = np.zeros(base_adj.shape, bool) if is_point else rasterize(start, profile)
    phi = np.where(start_mask, -1.0, 1.0).astype("float32")
    with rasterio.open(run_dir / "inputs" / "phi.tif", "w", **profile) as w:
        w.write(phi, 1)
    with rasterio.open(run_dir / "inputs" / "adj.tif", "w", **profile) as w:
        w.write((base_adj * adj).astype("float32"), 1)
    t_stop = t_start_s + hours * HOUR_S
    ign = (start.x, start.y) if is_point else None
    text = edit_namelist(deck.namelist, t_start_s, t_stop, ign, extra_ignitions)
    for key, value in (namelist or {}).items():
        text = _set(text, key, value)
    (run_dir / "elmfire.data").write_text(text, encoding="utf-8")
    env = {**os.environ, **deck.env}
    t0 = time.time()
    try:
        proc = subprocess.run(
            [deck.elmfire, "elmfire.data"],
            cwd=run_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"ELMFIRE가 {timeout_s:g}초 안에 끝나지 않았습니다({run_dir})") from None
    wall = time.time() - t0
    (run_dir / "elmfire.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    if proc.returncode != 0 or "successfully" not in proc.stdout:
        raise RuntimeError(f"ELMFIRE 실패({run_dir}): {(proc.stdout + proc.stderr)[-800:]}")
    toas = sorted((run_dir / "outputs").glob("time_of_arrival_*.tif"))
    if not toas:
        raise RuntimeError(f"도달 시간 래스터 없음: {run_dir}")
    with rasterio.open(toas[-1]) as r:
        toa = r.read(1).astype("float64")
        transform = r.transform
        cell_ha = abs(r.res[0] * r.res[1]) / 10_000
    toa[~(toa > 0)] = np.nan
    if is_point:  # the ignition cell itself
        start_mask = np.isfinite(toa) & (toa <= t_start_s + 1.0)
    return RunResult(
        run_dir,
        t_start_s,
        t_stop,
        adj,
        wall,
        toa,
        start_mask,
        transform,
        cell_ha,
        proc.stdout[-400:],
    )


def ranges(res: RunResult, start: BaseGeometry, hours: int = 8) -> list[BaseGeometry]:
    """P0 (the observed start, as given) and P1..P8 polygons cut from one run."""
    out = [start]
    for h in range(1, hours + 1):
        poly = polygonize(res.burned_by(res.t_start_s + h * HOUR_S), res.transform)
        out.append(unary_union([poly, out[-1]]))  # keep nesting exact after simplification
    return out


# ── calibration (회차마다 직전 구간, 11/7 사용자 결정) ─────────────────────


@dataclass
class Calibration:
    adj: float
    target_ha: float
    got_ha: float
    t_from_s: float
    t_to_s: float
    tries: list[tuple[float, float]]  # (adj, area ha)
    clipped: str | None = None  # "low" / "high" when the target was outside the search range


def calibrate(
    deck: Deck,
    work: Path,
    start: BaseGeometry,
    t_from_s: float,
    t_to_s: float,
    observed_to: BaseGeometry | None,
    lo: float = 0.2,
    hi: float = 80.0,
    rel_tol: float = 0.03,
    max_iter: int = 14,
    target_ha: float | None = None,
) -> Calibration:
    """One spread-rate multiplier so the run from `start` matches the next observed area.

    Target = area of (start ∪ observed perimeter at t_to) — the perimeters are drawn by hand
    and need not nest — or `target_ha` when given (e.g. growth-rate persistence for the first
    report, where only the ignition point came before). Search = bisection on log(adj).
    """
    if target_ha is None:
        target = unary_union([observed_to] if isinstance(start, Point) else [start, observed_to])
        target_ha = target.area / 10_000
    hours = (t_to_s - t_from_s) / HOUR_S
    tries: list[tuple[float, float]] = []

    def area(k: float) -> float:
        res = run(deck, Path(work) / f"adj_{k:.4f}", t_from_s, hours, start, k)
        a = res.area_ha(t_to_s)
        tries.append((k, a))
        shutil.rmtree(res.run_dir, ignore_errors=True)
        return a

    a_lo, a_hi = area(lo), area(hi)
    if target_ha <= a_lo:
        return Calibration(lo, target_ha, a_lo, t_from_s, t_to_s, tries, "low")
    if target_ha >= a_hi:
        return Calibration(hi, target_ha, a_hi, t_from_s, t_to_s, tries, "high")
    best = min(((lo, a_lo), (hi, a_hi)), key=lambda t: abs(t[1] - target_ha))
    for _ in range(max_iter):
        mid = float(np.sqrt(lo * hi))
        a = area(mid)
        if abs(a - target_ha) < abs(best[1] - target_ha):
            best = (mid, a)
        if abs(a - target_ha) <= rel_tol * target_ha:
            break
        lo, hi = (mid, hi) if a < target_ha else (lo, mid)
    return Calibration(best[0], target_ha, best[1], t_from_s, t_to_s, tries)
