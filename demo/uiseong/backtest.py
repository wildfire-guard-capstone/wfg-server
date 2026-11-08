"""확산 예측 보정 방법 비교: 회차 예측을 뒤 상황도 화선으로 검증한다(GPU 서버, ELMFIRE만).

    python -m demo.uiseong.backtest --input <demo-uiseong/input> --elmfire <실행 파일> --out <결과>
        [--grid 1,1.5,2,2.75,3.5,5,7] [--workers 4]

1. 격자: 회차마다 배율 격자로 8시간 예측 → 뒤 화선과 면적비 · IoU(어떤 배율이 맞았나)
2. 방법: step1_spread.CAL_METHODS 마다 회차 배율 → 8시간 예측 → 같은 검증.
   blend = interval과 rate 배율의 기하평균(계산만)
점수: 회차마다 |ln(예측 면적 ÷ 실측 면적)| 평균 → 회차 평균(작을수록 좋음), 치우침 = ln 평균
결과: <out>/backtest.json
"""

import argparse
import json
import math
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from demo.uiseong.data import MapData
from demo.uiseong.rounds import load
from demo.uiseong.step1_spread import CAL_METHODS, calibrate_round, validate
from spread import perimeter_run as pr


def score(checks: list[dict]) -> dict:
    logs = [math.log(c["pred_ha"] / c["obs_ha"]) for c in checks if c["pred_ha"] and c["obs_ha"]]
    return {
        "abs_log": sum(map(abs, logs)) / len(logs),
        "bias_log": sum(logs) / len(logs),
        "iou": sum(c["iou"] for c in checks) / len(checks),
    }


def one_round(deck, work: Path, md: MapData, rd, sc, grid: list[float]) -> dict:
    observed = md.report(rd.at)
    done: dict[float, dict] = {}

    def forecast(adj: float) -> dict:
        key = round(adj, 4)
        if key not in done:
            res = pr.run(deck, work / f"fcst_{rd.no}_{key}", rd.t_s, 8, observed, adj)
            checks = validate(md, rd, observed, res)
            areas = [round(res.area_ha(rd.t_s + h * 3600), 1) for h in range(9)]
            done[key] = {"adj": key, "areas_ha": areas, "checks": checks, **score(checks)}
            shutil.rmtree(res.run_dir, ignore_errors=True)
        return done[key]

    t0 = time.time()
    out = {"round": rd.no, "at": rd.at, "grid": [forecast(k) for k in grid], "methods": {}}
    adjs = {}
    for m in (m for m in CAL_METHODS if m != "blend"):  # blend는 interval · rate에서 계산만
        cal = calibrate_round(deck, work, md, rd, sc, m)
        adjs[m] = cal["adj"]
        out["methods"][m] = {"how": cal["from"], "parts": cal["parts"]}
    adjs["blend"] = math.sqrt(adjs["interval"] * adjs["rate"])
    out["methods"]["blend"] = {"how": "interval × rate 기하평균"}
    for m, k in adjs.items():
        out["methods"][m] |= forecast(k)
    out["wall_s"] = round(time.time() - t0, 1)
    print(f"회차 {rd.no} 끝 {out['wall_s']}초", flush=True)
    return out


def table(rounds: list[dict]) -> str:
    lines = []
    for r in rounds:
        lines.append(f"\n회차 {r['round']} {r['at']}")
        rows = [(f"격자 {g['adj']}", g) for g in r["grid"]]
        rows += [(f"{m}", v) for m, v in r["methods"].items()]
        for name, v in rows:
            cells = " ".join(f"{c['at']} {c['pred_ha']:.0f}/{c['obs_ha']:.0f}" for c in v["checks"])
            lines.append(
                f"  {name:<14} adj {v['adj']:6.3f} |ln| {v['abs_log']:.3f} "
                f"bias {v['bias_log']:+.3f} IoU {v['iou']:.3f} P8 {v['areas_ha'][-1]:.0f} · {cells}"
            )
    names = list(rounds[0]["methods"])
    lines.append("\n방법별(회차 평균)")
    for m in names:
        vs = [r["methods"][m] for r in rounds]
        lines.append(
            f"  {m:<11} |ln| {sum(v['abs_log'] for v in vs) / len(vs):.3f} "
            f"bias {sum(v['bias_log'] for v in vs) / len(vs):+.3f} "
            f"IoU {sum(v['iou'] for v in vs) / len(vs):.3f} "
            f"adj {[round(v['adj'], 2) for v in vs]}"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--deck", type=Path, help="기본 <input>/elmfire_deck")
    ap.add_argument("--elmfire", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--grid", default="1,1.5,2,2.75,3.5,5,7")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    md = MapData(a.input)
    sc = load(a.input)
    deck = pr.Deck(a.deck or a.input / "elmfire_deck", a.elmfire, env={})
    grid = [float(x) for x in a.grid.split(",")]
    with ThreadPoolExecutor(a.workers) as ex:
        rounds = list(
            ex.map(lambda rd: one_round(deck, a.out / "_elmfire", md, rd, sc, grid), sc.rounds)
        )
    (a.out / "backtest.json").write_text(
        json.dumps(rounds, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    text = table(rounds)
    (a.out / "backtest.txt").write_text(text, encoding="utf-8")
    print(text, flush=True)


if __name__ == "__main__":
    main()
