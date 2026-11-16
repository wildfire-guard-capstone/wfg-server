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
Pn is drawn along the time-of-arrival contour (not 30 m stairs) and widened evenly so its area
equals the burned-cell area. Holes are filled (a range is an envelope, like 산불영향구역),
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
        # ELMFIRE leaves no arrival time in the point-ignition cell (EC2 11/11: neighbours burned
        # at +10 · +12 · +22 min, the ignition cell stayed empty), so P1 missed the ignition
        # point. The fire starts there — count that cell as burned from the start.
        row, col = rasterio.transform.rowcol(transform, start.x, start.y)
        if 0 <= row < toa.shape[0] and 0 <= col < toa.shape[1]:
            start_mask[row, col] = True
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
    """P0 (the observed start, as given) and P1..P8 polygons cut from one run.

    Pn 모양 = 도착시간 등고선, 면적 = 30 m 칸 수(smooth_range).
    """
    out = [start]
    for h in range(1, hours + 1):
        poly = smooth_range(res, res.t_start_s + h * HOUR_S)
        # 누적. 등고선 좌표는 칸 격자에 안 맞아 합칠 때 cm² 단위 오차가 남을 수 있다
        # (저장 검사 check_slices는 면적 비율로 봐준다)
        out.append(unary_union([poly, out[-1]]))
    return out


# ── 매끈한 범위: 도착시간 등고선 모양 + 칸 면적 ──────────────────────────────


def _contour_segments(z: np.ndarray, level: float) -> list[tuple]:
    """칸 가운데 점을 꼭짓점으로 한 격자에서 z = level 선분(행 · 열 좌표).

    마칭 스퀘어: 네 꼭짓점의 안(z <= level) · 밖이 섞인 사각형마다 변 위 교점을 선형 보간으로
    찾아 잇는다. 안팎이 엇갈린 사각형(교점 4개)은 네 값의 평균으로 짝을 정한다.
    """
    a, b = z[:-1, :-1], z[:-1, 1:]  # 위 왼쪽 · 위 오른쪽
    c, d = z[1:, 1:], z[1:, :-1]  # 아래 오른쪽 · 아래 왼쪽
    ia, ib, ic, id_ = (a <= level), (b <= level), (c <= level), (d <= level)
    rr, cc = np.nonzero(~((ia == ib) & (ib == ic) & (ic == id_)))
    va, vb, vc, vd = a[rr, cc], b[rr, cc], c[rr, cc], d[rr, cc]
    ia, ib, ic, id_ = ia[rr, cc], ib[rr, cc], ic[rr, cc], id_[rr, cc]

    def t(p, q):
        with np.errstate(divide="ignore", invalid="ignore"):
            f = (level - p) / (q - p)
        return np.clip(np.nan_to_num(f, nan=0.5), 0.0, 1.0)

    # 변 0 위(a→b) · 1 오른쪽(b→c) · 2 아래(d→c) · 3 왼쪽(a→d)
    pts = [
        (rr + 0.0, cc + t(va, vb)),
        (rr + t(vb, vc), cc + 1.0),
        (rr + 1.0, cc + t(vd, vc)),
        (rr + t(va, vd), cc + 0.0),
    ]
    cut = [ia != ib, ib != ic, id_ != ic, ia != id_]
    centre_in = (va + vb + vc + vd) / 4 <= level
    segs = []
    for i in range(len(rr)):
        p = [(pts[e][0][i], pts[e][1][i]) for e in range(4) if cut[e][i]]
        if len(p) == 2:
            segs.append((p[0], p[1]))
        elif len(p) == 4:  # 안장점
            if ia[i] == centre_in[i]:
                segs += [(p[0], p[1]), (p[2], p[3])]
            else:
                segs += [(p[3], p[0]), (p[1], p[2])]
    return segs


def smooth_range(res: RunResult, t_s: float, simplify_m: float = 3.0) -> BaseGeometry:
    """t_s까지 탄 범위를 매끈한 경계로(구멍 메움, 덱 좌표계).

    1. 면적 기준 = 칸 범위(burned_by, 구멍 메움)의 칸 수 × 칸 면적 — 지금까지와 같은 값
    2. 모양 = 도착시간 등고선(칸 가운데 사이 선형 보간). 시작 화선 칸은 시작 시각으로 본다
    3. 등고선은 칸 바깥 모서리보다 반 칸쯤 안쪽을 지나므로, 단순화한 뒤 둘레를 고르게 넓혀
       1의 면적에 맞춘다(이분 탐색, 0 ~ 30 m)
    """
    from shapely import linestrings
    from shapely import polygonize as shp_polygonize

    mask = fill_holes_mask(res.burned_by(t_s))  # 1
    if not mask.any():
        return Polygon()
    target = float(mask.sum()) * abs(res.transform.a * res.transform.e)

    rows, cols = np.nonzero(mask)  # 2: 탄 곳 둘레만 잘라 계산
    r0, r1 = max(rows.min() - 2, 0), min(rows.max() + 3, mask.shape[0])
    c0, c1 = max(cols.min() - 2, 0), min(cols.max() + 3, mask.shape[1])
    big = t_s + 10 * HOUR_S
    win = (slice(r0, r1), slice(c0, c1))
    z = np.where(np.isfinite(res.toa[win]), res.toa[win], big)
    z = np.where(res.start_mask[win], np.minimum(z, res.t_start_s), z)
    z = np.where(mask[win], np.minimum(z, t_s), z)  # 메운 구멍은 안으로
    z = np.pad(z, 1, constant_values=big)  # 가장자리에서 선이 닫히게
    tr = res.transform
    off_r, off_c = r0 - 1 + 0.5, c0 - 1 + 0.5  # 패딩 1칸 · 칸 가운데

    def xy(p):
        return (tr.c + (p[1] + off_c) * tr.a, tr.f + (p[0] + off_r) * tr.e)

    lines = linestrings([[xy(p), xy(q)] for p, q in _contour_segments(z, t_s)])
    faces = shp_polygonize(lines)
    shape_ = unary_union([Polygon(g.exterior) for g in getattr(faces, "geoms", [faces])])
    if shape_.is_empty:
        return polygonize(mask, tr)
    if simplify_m:  # 3: 단순화를 먼저 해야 맞춘 면적이 그대로 남는다
        shape_ = shape_.simplify(simplify_m, preserve_topology=True)
    if shape_.area >= target:
        return shape_
    lo, hi = 0.0, 30.0
    for _ in range(20):
        mid = (lo + hi) / 2
        if shape_.buffer(mid, quad_segs=2).area < target:
            lo = mid
        else:
            hi = mid
    return shape_.buffer(hi, quad_segs=2)


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
