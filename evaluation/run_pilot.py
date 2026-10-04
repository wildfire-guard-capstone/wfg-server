"""First end-to-end evaluation: AI Hub case -> ELMFIRE -> P1–P8 -> metrics.

Runs inside the ELMFIRE worker image (needs the elmfire binary and GDAL):

  docker run --rm --platform linux/amd64 -v "$PWD:/app" -w /app wfg/elmfire-base:local bash -c \
    'export PATH=/opt/conda/envs/elmfire/bin:/elmfire/elmfire/build/linux/bin:$PATH PYTHONPATH=/app
     python -m evaluation.run_pilot --fbfm40 165'

DEM tiles (Copernicus GLO-30) go in data/dem/. Outputs (all git-ignored):
  runs/<case>_v0_fm<code>/            inputs, ELMFIRE outputs, deck.json, p1_p8.geojson
  results/pilot_v0_fm<code>/cases.csv  one row per case and cut, ELMFIRE and B2 scores
  results/pilot_v0_fm<code>/summary.json  medians
  results/pilot_v0_fm<code>/<case>.png  observed vs predicted (TP / FP / FN)
"""

import argparse
import csv
import glob
import json
import math
import os
import statistics
import subprocess
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.transform import from_origin
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union

from evaluation import metrics
from evaluation.baselines import wind_ellipse
from spread import slices
from spread.elmfire_inputs import CaseFiles, DeckConfig, build_deck
from spread.geo import projector, to_utm

ELMFIRE_BIN = os.environ.get("ELMFIRE_BIN", "elmfire_1.1")


def read_cases(scenario: Path) -> list[str]:
    """Case IDs from a scenario YAML (`- id: XX00000000` lines; avoids a yaml dependency)."""
    ids = []
    for line in scenario.read_text().splitlines():
        s = line.strip()
        if s.startswith("- id:"):
            ids.append(s.split(":", 1)[1].split("#")[0].strip())
    return ids


def run_elmfire(run_dir: Path) -> tuple[Path, float]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [ELMFIRE_BIN, "./inputs/elmfire.data"], cwd=run_dir, capture_output=True, text=True
    )
    elapsed = time.perf_counter() - t0
    (run_dir / "elmfire.log").write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"ELMFIRE failed ({proc.returncode}), see {run_dir}/elmfire.log")
    toa = sorted(glob.glob(str(run_dir / "outputs" / "time_of_arrival*.bil")))
    if not toa:
        raise RuntimeError(f"no time_of_arrival output in {run_dir}/outputs")
    return Path(toa[-1]), elapsed


def read_toa(path: Path, shp: tuple[int, int]) -> np.ndarray:
    with rasterio.open(path) as src:
        a = src.read(1).astype("float64")
    if a.shape != shp:
        raise ValueError(f"TOA shape {a.shape} != grid {shp}")
    a[(a < 0) | (a > 1e20)] = np.nan
    return a


def centroid_bearing(mask: np.ndarray, transform, origin: tuple[float, float]) -> float | None:
    rows, cols = np.nonzero(mask)
    if rows.size == 0:
        return None
    x = transform.c + (cols.mean() + 0.5) * transform.a
    y = transform.f + (rows.mean() + 0.5) * transform.e
    if math.hypot(x - origin[0], y - origin[1]) < 1e-6:
        return None
    return math.degrees(math.atan2(x - origin[0], y - origin[1])) % 360


def angle_diff(a: float | None, b: float | None) -> float:
    if a is None or b is None:
        return float("nan")
    d = abs(a - b) % 360
    return min(d, 360 - d)


def export_hourly(p: list[np.ndarray], transform, epsg: int, out: Path) -> list[float]:
    """P1..P8 as WGS84 GeoJSON; returns areas in ha."""
    back = projector(epsg, inverse=True)
    feats, areas = [], []
    for h, mask in enumerate(p, start=1):
        polys = [
            shape(g)
            for g, v in shapes(mask.astype("uint8"), mask=mask, transform=transform)
            if v == 1
        ]
        geom = unary_union(polys) if polys else None
        areas.append(mask.sum() * abs(transform.a * transform.e) / 1e4)
        if geom is not None:
            feats.append(
                {
                    "type": "Feature",
                    "properties": {"hour": h, "area_ha": areas[-1]},
                    "geometry": mapping(shp_transform(back, geom)),
                }
            )
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return areas


def plot(path: Path, truth, pred, b2, ign_rc, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    union = truth | pred | b2
    rows, cols = np.nonzero(union)
    pad = 10
    r0, r1 = max(rows.min() - pad, 0), min(rows.max() + pad, union.shape[0])
    c0, c1 = max(cols.min() - pad, 0), min(cols.max() + pad, union.shape[1])

    def rgb(p):
        img = np.ones((*truth.shape, 3))
        img[p & truth] = (0.2, 0.65, 0.3)  # TP green
        img[p & ~truth] = (0.95, 0.55, 0.2)  # FP orange (overprediction)
        img[~p & truth] = (0.25, 0.45, 0.9)  # FN blue (missed)
        return img[r0:r1, c0:c1]

    fig, axes = plt.subplots(1, 2, figsize=(10, 5))
    for ax, p, name in ((axes[0], pred, "ELMFIRE"), (axes[1], b2, "B2 wind ellipse")):
        ax.imshow(rgb(p), interpolation="nearest")
        ax.plot(ign_rc[1] - c0, ign_rc[0] - r0, "k*", ms=12)
        ax.set_title(name)
        ax.set_xticks([])
        ax.set_yticks([])
    fig.suptitle(f"{title}\ngreen = hit, orange = over, blue = missed (area-matched)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def evaluate_case(case_id: str, args, cfg: DeckConfig) -> list[dict]:
    case = CaseFiles.load(args.cases_dir / case_id)
    run_dir = args.runs_dir / f"{case_id}_{args.tag}"
    deck = build_deck(case, run_dir, args.dem_dir, cfg)
    toa_path, elapsed = run_elmfire(run_dir)

    shp = tuple(deck["shape"])
    xmin, ymin, xmax, ymax = deck["bounds"]
    tr = from_origin(xmin, ymax, deck["cell_m"], deck["cell_m"])
    toa = read_toa(toa_path, shp)

    perim = to_utm(case.perimeter_wgs84, deck["epsg"])
    truth = rasterize([(perim, 1)], out_shape=shp, transform=tr, fill=0).astype(bool)
    ign = tuple(deck["ignition_utm"])
    ign_rc = ((ymax - ign[1]) / deck["cell_m"], (ign[0] - xmin) / deck["cell_m"])

    p = slices.hourly(toa, 8)
    p_areas = export_hourly(p, tr, deck["epsg"], run_dir / "p1_p8.geojson")
    nested = all((p[i] & ~p[i + 1]).sum() == 0 for i in range(7))

    target = int(truth.sum())
    cell_m2 = deck["cell_m"] ** 2
    am, t_star = slices.area_matched(toa, target)
    dur_s = (case.duration_h or 0) * 3600
    dur = slices.burned(toa, dur_s)

    n = len(deck["ws_mph"])
    hours = max(1, min(n, math.ceil(case.duration_h or 1)))
    b2 = wind_ellipse(
        shp, tr, ign, target * cell_m2, deck["ws_mph"][:hours], deck["wd_deg"][:hours]
    )

    truth_dir = centroid_bearing(truth, tr, ign)
    rows = []
    for cut, pred in (("area_matched", am), ("observed_duration", dur)):
        s = metrics.score(pred, truth).as_dict()
        row = {
            "case_id": case_id,
            "cut": cut,
            **{k: round(v, 4) for k, v in s.items()},
            "direction_error_deg": round(angle_diff(centroid_bearing(pred, tr, ign), truth_dir), 1),
            "observed_ha": round(target * cell_m2 / 1e4, 2),
            "predicted_ha": round(pred.sum() * cell_m2 / 1e4, 2),
            "t_star_h": round(t_star / 3600, 2) if (cut == "area_matched" and t_star) else "",
            "reached": "" if cut != "area_matched" else bool(t_star),
            "duration_h": round(case.duration_h or 0, 2),
            "runtime_s": round(elapsed, 1),
            "p1_p8_ha": ";".join(f"{a:.1f}" for a in p_areas),
            "p_nested": nested,
        }
        if cut == "area_matched":
            sb = metrics.score(b2, truth)
            row["b2_iou"] = round(sb.iou, 4)
            row["b2_f2"] = round(sb.f2, 4)
            row["b2_direction_error_deg"] = round(
                angle_diff(centroid_bearing(b2, tr, ign), truth_dir), 1
            )
            row["skill_iou_vs_b2"] = round(metrics.skill(s["iou"], sb.iou), 4)
        rows.append(row)

    args.out.mkdir(parents=True, exist_ok=True)
    plot(args.out / f"{case_id}.png", truth, am, b2, ign_rc, f"{case_id}  IoU {rows[0]['iou']:.2f}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=Path, default=Path("evaluation/scenarios/pilot.yaml"))
    ap.add_argument("--cases", nargs="*", help="override case IDs")
    ap.add_argument("--cases-dir", type=Path, default=Path("cases"))
    ap.add_argument("--dem-dir", type=Path, default=Path("data/dem"))
    ap.add_argument("--runs-dir", type=Path, default=Path("runs"))
    ap.add_argument("--out", type=Path, default=None, help="default results/pilot_v0_fm<code>")
    ap.add_argument("--fbfm40", type=int, default=DeckConfig().fbfm40, help="uniform fuel code")
    args = ap.parse_args()

    ids = args.cases or read_cases(args.scenario)
    cfg = DeckConfig(fbfm40=args.fbfm40)
    args.out = args.out or Path(f"results/pilot_v0_fm{args.fbfm40}")
    args.tag = f"v0_fm{args.fbfm40}"
    all_rows, failed = [], {}
    for cid in ids:
        print(f"[{cid}] running ...", flush=True)
        try:
            rows = evaluate_case(cid, args, cfg)
        except Exception as e:  # keep going: one bad case must not stop the batch
            failed[cid] = repr(e)
            print(f"[{cid}] FAILED: {e!r}", flush=True)
            continue
        all_rows += rows
        r = rows[0]
        print(
            f"[{cid}] IoU {r['iou']:.3f}  F2 {r['f2']:.3f}  B2 IoU {r['b2_iou']:.3f}  "
            f"dir err {r['direction_error_deg']}°  run {r['runtime_s']} s",
            flush=True,
        )

    args.out.mkdir(parents=True, exist_ok=True)
    if all_rows:
        keys = list(dict.fromkeys(k for r in all_rows for k in r))
        with (args.out / "cases.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(all_rows)

    def med(cut: str, key: str) -> float | None:
        vals = [r[key] for r in all_rows if r["cut"] == cut and isinstance(r.get(key), float)]
        vals = [v for v in vals if not math.isnan(v)]
        return round(statistics.median(vals), 4) if vals else None

    summary = {
        "scenario": str(args.scenario),
        "fuel": f"v0 uniform FBFM40 {args.fbfm40}",
        "cases": len({r["case_id"] for r in all_rows}),
        "failed": failed,
        "area_matched": {
            k: med("area_matched", k)
            for k in (
                "iou",
                "f1",
                "f2",
                "precision",
                "recall",
                "kappa",
                "direction_error_deg",
                "b2_iou",
                "b2_f2",
                "b2_direction_error_deg",
                "skill_iou_vs_b2",
            )
        },  # fmt: skip
        "observed_duration": {
            k: med("observed_duration", k) for k in ("iou", "f2", "recall", "area_ratio")
        },
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
