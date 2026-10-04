"""Scenario evaluation: AI Hub cases -> ELMFIRE -> P1–P8 -> metrics (pilot, dev, ...).

Runs inside the ELMFIRE worker image (needs the elmfire binary and GDAL):

  docker run --rm --platform linux/amd64 -v "$PWD:/app" -w /app wfg/elmfire-base:local bash -c \
    'export PATH=/opt/conda/envs/elmfire/bin:/elmfire/elmfire/build/linux/bin:$PATH PYTHONPATH=/app
     python -m evaluation.run_pilot --scenario evaluation/scenarios/dev.yaml \
         --fuel-source case_majority'

DEM tiles (Copernicus GLO-30) go in data/dem/. Outputs (all git-ignored):
  runs/<case>_<inputs>/             inputs, ELMFIRE outputs, deck.json, p1_p8.geojson
  results/<scenario>__<inputs>/      cases.csv · summary.json (medians + bootstrap 95 % CI) · PNGs
  results/index.csv                 one line per run, appended (the improvement log)
"""

import argparse
import csv
import dataclasses
import glob
import json
import math
import os
import shutil
import statistics
import subprocess
import time
from datetime import datetime
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
from spread import ensemble, slices
from spread.elmfire_inputs import CaseFiles, DeckConfig, build_deck
from spread.geo import projector, to_utm

ELMFIRE_BIN = os.environ.get("ELMFIRE_BIN", "elmfire_1.1")
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 0


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
    meta = json.loads((args.cases_dir / case_id / "meta.json").read_text())
    run_dir = args.runs_dir / f"{case_id}_{args.tag}"
    deck = build_deck(case, run_dir, args.dem_dir, cfg)
    toa_path, elapsed = run_elmfire(run_dir)

    shp = tuple(deck["shape"])
    xmin, ymin, xmax, ymax = deck["bounds"]
    tr = from_origin(xmin, ymax, deck["cell_m"], deck["cell_m"])
    toa = read_toa(toa_path, shp)

    perim = to_utm(case.perimeter_wgs84, deck["epsg"])
    truth = rasterize([(perim, 1)], out_shape=shp, transform=tr, fill=0).astype(bool)

    # Plan step 1-3: if the run stopped before reaching the observed area, rerun to max_hours
    extended = False
    if slices.area_matched(toa, int(truth.sum()))[1] is None and not cfg.extend_to_max:
        cfg = dataclasses.replace(cfg, extend_to_max=True)
        deck = build_deck(case, run_dir, args.dem_dir, cfg)
        toa_path, more = run_elmfire(run_dir)
        elapsed += more
        toa = read_toa(toa_path, shp)
        extended = True
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

    # Plan I-5: wind ensemble. Member 0 is the run above; the area-matched prediction becomes
    # the most probable cells, the duration cut stays deterministic.
    if args.ensemble > 1:
        base_wx = list(zip(deck["ws_mph_obs"], deck["wd_deg"], strict=True))
        members = ensemble.perturb(base_wx, args.ensemble, args.wd_sigma, args.ws_frac, args.seed)
        masks, toas = [am], [toa]
        for i, wx in enumerate(members[1:], start=1):
            mdir = args.runs_dir / f"{case_id}_{args.tag}_m{i}"
            build_deck(case, mdir, args.dem_dir, cfg, wx_override=wx)
            mpath, more = run_elmfire(mdir)
            elapsed += more
            mtoa = read_toa(mpath, shp)
            masks.append(slices.area_matched(mtoa, target)[0])
            toas.append(mtoa)
            shutil.rmtree(mdir)
        prob = ensemble.burn_probability(masks)
        with np.errstate(all="ignore"):
            mean_toa = np.nanmean(np.stack(toas), axis=0)
        am = ensemble.top_cells(prob, target, tiebreak=mean_toa)
        np.save(run_dir / "burn_probability.npy", prob.astype("float32"))

    n = len(deck["ws_mph_obs"])
    hours = max(1, min(n, math.ceil(case.duration_h or 1)))
    b2 = wind_ellipse(
        shp, tr, ign, target * cell_m2, deck["ws_mph_obs"][:hours], deck["wd_deg"][:hours]
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
            "tstop_h": round(deck["tstop_s"] / 3600, 1),
            "extended": extended,
            "weather_stations": ";".join(meta.get("weather_stations") or []),
            "p1_p8_ha": ";".join(f"{a:.1f}" for a in p_areas),
            "fbfm40": deck["fbfm40"],
            "m1": deck["moisture_pct"]["m1"],
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
    ap.add_argument("--out", type=Path, default=None, help="default results/<scenario>__<inputs>")
    ap.add_argument("--index", type=Path, default=Path("results/index.csv"))
    d = DeckConfig()
    ap.add_argument("--fbfm40", type=int, default=d.fbfm40, help="uniform / fallback fuel code")
    ap.add_argument("--fuel-source", default=d.fuel_source, choices=["uniform", "case_majority"])
    ap.add_argument(
        "--moisture-source", default=d.moisture_source, choices=["fixed", "aihub_points"]
    )
    ap.add_argument("--wind-mult", type=float, default=d.wind_mult)
    ap.add_argument("--canopy-source", default=d.canopy_source, choices=["zero", "aihub_points"])
    ap.add_argument("--phiw-adj", type=float, default=d.phiw_adj, help="ELMFIRE PHIW_ADJ")
    ap.add_argument("--phis-adj", type=float, default=d.phis_adj, help="ELMFIRE PHIS_ADJ")
    ap.add_argument("--ensemble", type=int, default=1, help="members (1 = deterministic)")
    ap.add_argument("--wd-sigma", type=float, default=30.0, help="ensemble wind direction sd (deg)")
    ap.add_argument("--ws-frac", type=float, default=0.2, help="ensemble wind speed +- fraction")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    ids = args.cases or read_cases(args.scenario)
    cfg = DeckConfig(
        fbfm40=args.fbfm40,
        fuel_source=args.fuel_source,
        moisture_source=args.moisture_source,
        wind_mult=args.wind_mult,
        canopy_source=args.canopy_source,
        phiw_adj=args.phiw_adj,
        phis_adj=args.phis_adj,
    )
    scenario = "custom" if args.cases else args.scenario.stem
    if scenario == "holdout" and not os.environ.get("WFG_OPEN_HOLDOUT"):
        raise SystemExit("holdout is locked until calibration is done (set WFG_OPEN_HOLDOUT=1)")
    args.label = cfg.label() + (
        f"_ens-{args.ensemble}x{args.wd_sigma:g}deg" if args.ensemble > 1 else ""
    )
    args.out = args.out or Path(f"results/{scenario}__{args.label}")
    args.tag = args.label
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

    def values(cut: str, key: str) -> list[float]:
        vals = [r[key] for r in all_rows if r["cut"] == cut and isinstance(r.get(key), float)]
        return [v for v in vals if not math.isnan(v)]

    def med(cut: str, key: str) -> float | None:
        vals = values(cut, key)
        return round(statistics.median(vals), 4) if vals else None

    def ci(cut: str, key: str) -> list[float] | None:
        """Bootstrap 95 % CI of the median over cases (resampling cases)."""
        vals = np.array(values(cut, key))
        if vals.size < 3:
            return None
        rng = np.random.default_rng(BOOTSTRAP_SEED)
        meds = np.median(rng.choice(vals, size=(BOOTSTRAP_N, vals.size)), axis=1)
        return [
            round(float(np.percentile(meds, 2.5)), 4),
            round(float(np.percentile(meds, 97.5)), 4),
        ]

    summary = {
        "scenario": scenario,
        "inputs": args.label,
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
        "area_matched_ci95": {k: ci("area_matched", k) for k in ("iou", "f2", "b2_iou")},
        "observed_duration": {
            k: med("observed_duration", k) for k in ("iou", "f2", "recall", "area_ratio")
        },
        "not_reached_after_extension": sorted(
            r["case_id"] for r in all_rows if r["cut"] == "area_matched" and not r["reached"]
        ),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    append_index(args.index, args, scenario, summary, all_rows)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def git_commit() -> str:
    """Current commit read from .git directly (the worker image has no git binary)."""
    head = Path(".git/HEAD")
    if not head.exists():
        return ""
    ref = head.read_text().strip()
    if ref.startswith("ref: "):
        ref_path = Path(".git") / ref[5:]
        if ref_path.exists():
            return ref_path.read_text().strip()[:7]
        for line in Path(".git/packed-refs").read_text().splitlines():
            if line.endswith(ref[5:]):
                return line[:7]
        return ""
    return ref[:7]


def append_index(path: Path, args, scenario: str, summary: dict, rows: list[dict]) -> None:
    """Plan step 1-6: one line per evaluation run — the improvement log."""
    am = summary["area_matched"]
    ci_iou = summary["area_matched_ci95"]["iou"] or ["", ""]
    line = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "commit": git_commit(),
        "scenario": scenario,
        "inputs": args.label,
        "cases": summary["cases"],
        "failed": len(summary["failed"]),
        "not_reached": len(summary["not_reached_after_extension"]),
        "iou_median": am["iou"],
        "iou_ci_low": ci_iou[0],
        "iou_ci_high": ci_iou[1],
        "f2_median": am["f2"],
        "direction_error_median": am["direction_error_deg"],
        "b2_iou_median": am["b2_iou"],
        "runtime_s_total": round(
            sum(r["runtime_s"] for r in rows if r["cut"] == "area_matched"), 1
        ),
        "out": str(args.out),
    }
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(line))
        if new:
            w.writeheader()
        w.writerow(line)


if __name__ == "__main__":
    main()
