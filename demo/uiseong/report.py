"""데모 결과 요약(마크다운): 회차별 예측 · 검증 · 항목 상태 · 재시도 · LLM 시간.

python -m demo.uiseong.report --runs <결과 폴더> [--out 요약.md]
"""

import argparse
import json
from pathlib import Path

STATE = {"ok": "생성", "reused": "재사용", "failed": "실패", "no_llm": "LLM 없음"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    lines = ["# 의성 데모 실행 요약", ""]
    head = (
        "| 회차 | 보정 배율 | P0 / P1 / P5 / P8 (ha) | 현재 · P5 단계 | 지휘권자 "
        "| 항목 상태 | LLM |"
    )
    lines += [head, "|---|---|---|---|---|---|---|"]
    detail = []
    for p in sorted(a.runs.glob("round_*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        rd, sp, cal = r["round"], r["spread"], r["calibration"]
        b = r["llm_input"]["bundles"]
        ar = sp["areas_ha"]
        items = r["proposal"] or []
        states = " · ".join(f"{i['id']} {STATE.get(i['state'], i['state'])}" for i in items)
        calls = r.get("llm_calls") or []
        tok = sum(c.get("completion_tokens") or 0 for c in calls)
        lines.append(
            f"| {rd['at']} {rd['version']} | {cal['adj']:.2f} | "
            f"{ar[0]} / {ar[1]} / {ar[5]} / {ar[8]} | "
            f"{b['stage']['now']['stage']} · {b['stage']['p5']['stage']} | "
            f"{b['commander']['now']['title']} → {b['commander']['next']['title']} | {states} | "
            f"{len(calls)}회 · {tok}토큰 · {r['timing']['llm_s']:.0f}초 |"
        )
        detail.append(f"\n## {rd['at']} {rd['version']} ({rd['report']}, {rd['reader']})\n")
        ps = cal.get("parts", [cal])
        parts = "; ".join(
            (f"{p['from']} " if len(ps) > 1 else "")
            + f"목표 {p['target_ha']} ha → {p['got_ha']} ha · 배율 {p['adj']:.2f}"
            for p in ps
        )
        detail.append(f"- 보정: {cal['from']} → 배율 {cal['adj']:.2f} ({parts})")
        v = "; ".join(
            f"{c['at']} 예측 {c['pred_ha']} / 상황도 {c['obs_ha']} ha (IoU {c['iou']})"
            for c in sp["validation"]
        )
        detail.append(f"- 검증: {v or '없음'}")
        for lg in r.get("agent_log") or []:
            errs = [e for at in lg["attempts"] for e in at["errors"]]
            if errs:
                detail.append(
                    f"- {lg['id']} 시도 {len(lg['attempts'])}회: " + " / ".join(errs)[:400]
                )
        for it in items:
            for s in it["sections"]:
                for bl in s["blocks"]:
                    if bl["type"] == "paragraph":
                        detail.append(f"  - {bl['action']} [{bl['category']}] {bl['text']}")
    text = "\n".join(lines + detail) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
