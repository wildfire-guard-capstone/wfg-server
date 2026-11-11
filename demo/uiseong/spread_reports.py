"""상황도 1 ~ 6보 화선에서 각각 다시 출발해 8시간 확산 예측(위험도 비교용, GPU 서버).

main(최종 설정, step1_spread "fixed") 코드로 돌린다.
run_demo.py는 scenario.json의 1 ~ 4보만 돌리므로 5보(16:30) · 6보(19:00)는
perimeters.json "later"의 화선에서 같은 방식으로 출발시킨다.

    cd ~/wfg/demo/wfg-server            # main
    PYTHONPATH=. python /경로/spread_reports.py --input ~/wfg/demo/input --deck ~/wfg/demo/deck \\
        --elmfire ~/wfg/elmfire/bin/elmfire --out ~/wfg/demo/runs/reports16 [--reports 1-6]

결과: <out>/round_k/01_spread/spread.json(run_demo와 같은 형식) · <out>/reports.json(k → 시각 · 보)
그다음 위험도: step1b_risk --runs <out> --reports 1-6
"""

import argparse
import dataclasses
import json
from pathlib import Path

from demo.uiseong import rounds as rounds_mod
from demo.uiseong import step1_spread
from demo.uiseong.data import MapData
from spread import perimeter_run as pr


def report_times(input_dir: Path) -> list[tuple[int, str]]:
    per = json.loads((input_dir / "perimeters.json").read_text(encoding="utf-8"))
    return [(int(r["no"]), r["at"]) for r in per["reports"] + per["later"]]


def parse_range(text: str) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        a, _, b = part.partition("-")
        out |= set(range(int(a), int(b or a) + 1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument(
        "--deck", type=Path, help="기본 <input>/elmfire_deck. 최종 설정: ~/wfg/demo/deck"
    )
    ap.add_argument("--elmfire", default="elmfire")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--reports", default="1-6", help="몇 보에서 출발할지(예: 1-6, 5,6)")
    ap.add_argument("--calibration", default="fixed", choices=list(step1_spread.CAL_METHODS))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    sc = rounds_mod.load(a.input)
    md = MapData(a.input)
    deck = pr.Deck(a.deck or a.input / "elmfire_deck", a.elmfire, env={})
    want = parse_range(a.reports)
    meta = {}
    template = sc.rounds[-1]  # 기상 밴드 · 날짜 등 공통 값만 빌림(fixed 보정은 회차 값을 쓰지 않음)
    for no, at in report_times(a.input):
        if no not in want:
            continue
        rd = next((r for r in sc.rounds if r.at == at), None) or dataclasses.replace(
            template, no=no, at=at, report=f"{no}보", version=f"r{no}", note="spread_reports"
        )
        rd = dataclasses.replace(rd, no=no)
        res = step1_spread.run_spread(deck, a.out / "_elmfire", md, rd, sc, a.calibration)
        step1_spread.save(res, a.out / f"round_{no}" / "01_spread")
        meta[no] = {"at": at, "report": f"{no}보", "p5_ha": res.areas_ha[5]}
        print(f"{no}보 {at}: P5 {res.areas_ha[5]} ha · {res.wall_s}초", flush=True)
    (a.out / "reports.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
