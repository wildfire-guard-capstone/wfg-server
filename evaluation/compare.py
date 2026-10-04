"""Compare two evaluation runs case by case and apply the adoption rule (plan track I).

Rule: adopt the new inputs when the median paired ΔIoU (area-matched) is > 0 AND at
least MIN_WINS of the cases improve. Realism changes (e.g. non-burnable mask) may be
adopted when they are "not worse" — that call is made by hand, this tool prints both.

Usage:
  python -m evaluation.compare results/dev__fuel-fm165_... results/dev__fuel-case_majority_...
"""

import argparse
import csv
import statistics
import sys
from pathlib import Path

MIN_WINS = 12  # of 20 development cases
EPS = 1e-4  # differences smaller than this count as ties


def load(run_dir: Path, key: str = "iou") -> dict[str, float]:
    with (run_dir / "cases.csv").open() as f:
        return {
            r["case_id"]: float(r[key]) for r in csv.DictReader(f) if r["cut"] == "area_matched"
        }


def compare(base: dict[str, float], new: dict[str, float], min_wins: int = MIN_WINS) -> dict:
    common = sorted(base.keys() & new.keys())
    if not common:
        raise ValueError("no common cases")
    deltas = {c: new[c] - base[c] for c in common}
    wins = sum(d > EPS for d in deltas.values())
    losses = sum(d < -EPS for d in deltas.values())
    med = statistics.median(deltas.values())
    return {
        "cases": len(common),
        "median_base": statistics.median(base[c] for c in common),
        "median_new": statistics.median(new[c] for c in common),
        "median_delta": med,
        "wins": wins,
        "losses": losses,
        "ties": len(common) - wins - losses,
        "adopt": med > 0 and wins >= min_wins,
        "not_worse": med >= -EPS,
        "deltas": deltas,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("--metric", default="iou")
    ap.add_argument("--min-wins", type=int, default=MIN_WINS)
    args = ap.parse_args()

    r = compare(load(args.base, args.metric), load(args.new, args.metric), args.min_wins)
    for c, d in sorted(r["deltas"].items(), key=lambda kv: kv[1]):
        print(f"  {c}  {d:+.3f}")
    print(
        f"{args.metric}: median {r['median_base']:.4f} -> {r['median_new']:.4f} "
        f"(paired Δ median {r['median_delta']:+.4f}) · wins {r['wins']} / losses {r['losses']} "
        f"/ ties {r['ties']} of {r['cases']}"
    )
    print("ADOPT" if r["adopt"] else ("not worse" if r["not_worse"] else "REJECT"))
    sys.exit(0)


if __name__ == "__main__":
    main()
