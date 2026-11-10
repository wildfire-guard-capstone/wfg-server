# ruff: noqa: E501
"""의성 데모 실행(GPU 서버): 입력 → 1 확산 예측 → 2 규칙 판정 → 3 LLM 대응 생성 → 출력.

    python -m demo.uiseong.run_demo --input <demo-uiseong/input> --elmfire <실행 파일> --out <결과>
        [--rounds 1,2,3,4] [--items S1,E2] [--reuse-spread <앞 결과 폴더>] [--spread-only] [--skip-llm]
        [--calibration fixed|blend|interval|rate|from_first] [--k 8] [--llm …:8031] [--evidence …:8032]

입력 폴더(input/): scenario.json(사고 · 회차 · 현장 보고) · perimeters.json(상황도 화선) · regions.json ·
map/(지도 레이어) · elmfire_deck/(ELMFIRE 입력). 자세한 것은 input/README.md.

결과 폴더(--out):
  round_k/01_spread/   spread.json(보정 · P0~P8 · 검증, 다시 쓰기용 WKT) · ranges_4326.geojson(hour 0 ~ 8)
                       · weather.json(예측에 쓴 기상) — 두 파일이 곧 EC2 file 예측의 입력(README 「EC2로 넘기기」)
  round_k/02_rules/    inputs.json(규칙 엔진에 넣은 값) · llm_input.json(규칙 엔진 출력: bundles · common · items)
  round_k/03_rag/      <항목>.json(질의 · 찾은 근거)
  round_k/04_llm/      <항목>.json(보낸 메시지 · 답 원문 · 검사) · <항목>_prompt.txt(사람이 읽는 판)
  round_k/05_output/   proposal.json(화면용 제안서)
  round_k.json         회차 전부(뷰어 입력) · summary.json · run.log

디버깅: --reuse-spread로 앞 결과의 01_spread를 다시 쓰면 ELMFIRE 없이 규칙 · 제안만 돈다.
--items로 항목 몇 개만 돌린다. 각 단계 출력 폴더를 열어 보면 어디서 틀렸는지 보인다.
"""

import argparse
import json
import time
from datetime import timedelta
from pathlib import Path

from agent.clients import LLM, Evidence
from agent.generate import Agent
from demo.uiseong import prediction_weather, step1_spread, step2_rules, step3_agent
from demo.uiseong import rounds as rounds_mod
from demo.uiseong.data import MapData, to4326
from spread import perimeter_run as pr


def geo4326(g) -> dict:
    from shapely.geometry import mapping

    return mapping(to4326(g))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path, required=True, help="demo-uiseong/input")
    ap.add_argument("--deck", type=Path, help="ELMFIRE 입력(기본 <input>/elmfire_deck)")
    ap.add_argument("--elmfire", default="elmfire")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rounds", default="")
    ap.add_argument("--items", default="", help="이 항목만(예: S1,E2). 비우면 전부")
    ap.add_argument("--reuse-spread", type=Path, help="앞 결과 폴더: round_k/01_spread를 다시 쓴다")
    ap.add_argument("--spread-only", action="store_true", help="1단계만")
    ap.add_argument("--skip-llm", action="store_true", help="1 · 2단계만(제안 생성 없음)")
    ap.add_argument("--calibration", default="fixed", choices=list(step1_spread.CAL_METHODS))
    ap.add_argument("--llm", default="http://127.0.0.1:8031")
    ap.add_argument("--evidence", default="http://127.0.0.1:8032")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--k", type=int, default=10, help="항목마다 검색 근거 수")
    ap.add_argument("--thinking", action="store_true", help="EXAONE 추론 모드(기본 끔)")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    deck_dir = a.deck or a.input / "elmfire_deck"
    work = a.out / "_elmfire"
    sc = rounds_mod.load(a.input)
    md = MapData(a.input)
    deck = pr.Deck(deck_dir, a.elmfire, env={})
    only = {x.strip() for x in a.items.split(",") if x.strip()} or None
    agent = None
    if not (a.skip_llm or a.spread_only):
        agent = Agent(
            LLM(a.llm),
            Evidence(a.evidence),
            step3_agent.known_names(md),
            workers=a.workers,
            k=a.k,
            thinking=a.thinking,
        )
    t_all = time.time()
    print(f"시나리오: {sc.title} · 회차 {[r.at for r in sc.rounds]}", flush=True)
    print(
        f"지도 자료: 마을 {len(md.villages)} · 주택 {len(md.houses[0])} · 대피소 {len(md.shelters)} · "
        f"취약시설 {len(md.vulnerable)} · 인접 시군 {md.neighbors['의성군']}",
        flush=True,
    )
    wanted = {int(x) for x in a.rounds.split(",") if x.strip()} or {r.no for r in sc.rounds}
    prev_stage, prev_alerts, prev, summary = None, (), {}, []
    for rd in sc.rounds:
        if rd.no not in wanted:
            continue
        t_round = time.time()
        rdir = a.out / f"round_{rd.no}"
        print(f"\n=== 회차 {rd.no} {rd.version} {rd.at} ({rd.report}, {rd.reader}) ===", flush=True)
        # 1. 확산 예측
        if a.reuse_spread:
            sp = step1_spread.load(a.reuse_spread / f"round_{rd.no}" / "01_spread")
            print(
                f"  1 확산: {a.reuse_spread.name}의 결과를 다시 씀 · 배율 {sp.calibration['adj']:.3f}",
                flush=True,
            )
        else:
            sp = step1_spread.run_spread(deck, work, md, rd, sc, a.calibration)
            for p in sp.calibration["parts"]:
                print(
                    f"  1 보정 {p['from']}: adj {p['adj']:.3f} · 목표 {p['target_ha']} → {p['got_ha']} ha",
                    flush=True,
                )
            print(
                f"  1 확산: 배율 {sp.calibration['adj']:.3f} ({sp.calibration['method']}) · {sp.wall_s}초",
                flush=True,
            )
        weather = None  # EC2 file 예측으로 넘길 「예측에 쓴 기상」
        asos_csv = a.input / "risk" / "weather_hourly.csv"
        if asos_csv.exists():
            weather = prediction_weather.weather_used(
                deck_dir, md.ignition, rd.base_at, rd.band1, asos_csv
            )
        else:
            print(f"    weather.json 건너뜀: {asos_csv} 없음", flush=True)
        step1_spread.save(sp, rdir / "01_spread", weather)
        print(f"    P0~P8 {sp.areas_ha} ha", flush=True)
        if weather:
            print(
                "    기상(weather.json) 풍향 · 풍속 "
                + " ".join(f"{h['wind_dir']:.0f}°/{h['wind_speed']}" for h in weather["hours"]),
                flush=True,
            )
        print(
            f"    검증 {[(c['at'], c['pred_ha'], c['obs_ha'], c['iou']) for c in sp.validation]}",
            flush=True,
        )
        if a.spread_only:
            summary.append(
                {
                    "round": rd.no,
                    "at": rd.at,
                    "adj": sp.calibration["adj"],
                    "areas_ha": sp.areas_ha,
                    "validation": sp.validation,
                }
            )
            continue
        # 2. 규칙 판정
        ru = step2_rules.run_rules(md, rd, sc, sp, deck_dir, prev_stage, prev_alerts)
        (rdir / "02_rules").mkdir(parents=True, exist_ok=True)
        for name, obj in (("inputs.json", ru["inputs"]), ("llm_input.json", ru["llm_input"])):
            (rdir / "02_rules" / name).write_text(
                json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
            )
        llm_in = ru["llm_input"]
        print(
            f"  2 규칙: {ru['seconds']}초 · 현재 {ru['stage_now']} · P5 {ru['stage_p5']} · 지휘권자 {ru['commander']} · "
            f"항목 {[i['id'] for i in llm_in['items']]}",
            flush=True,
        )
        if "돌풍" in ru["weather"]["wind_source"]:  # 최종 덱에 시간평균 ws · wd가 없을 때
            print(f"  ! 규칙 엔진 바람: {ru['weather']['wind_source']}", flush=True)
        prev_stage, prev_alerts = ru["next_prev_stage"], ru["next_prev_alerts"]
        # 3. LLM 대응 생성
        items, logs, llm_s, calls = [], [], 0.0, []
        if agent is not None:
            n0 = len(agent.llm.calls)
            t_llm = time.time()
            items, logs = step3_agent.run_agent(agent, llm_in, prev, only)
            llm_s = time.time() - t_llm
            calls = agent.llm.calls[n0:]
            step3_agent.save(items, logs, rdir)
            prev |= step3_agent.next_prev(items, rd.version)
            states = {
                i[
                    "id"
                ]: f"{i['state']}({sum(b['type'] == 'paragraph' for s in i['sections'] for b in s['blocks'])})"
                for i in items
            }
            print(f"  3 제안: {llm_s:.0f}초 · 호출 {len(calls)}회 · {states}", flush=True)
        # 출력(뷰어 입력)
        out = {
            "round": {
                "no": rd.no,
                "version": rd.version,
                "at": rd.at,
                "report": rd.report,
                "reader": rd.reader,
                "note": rd.note,
                "base_at": rd.base_at.isoformat(),
                "slices_until": [
                    (rd.base_at + timedelta(hours=h)).strftime("%H:%M") for h in range(9)
                ],
            },
            "calibration": sp.calibration,
            "spread": {
                "areas_ha": sp.areas_ha,
                "wall_s": sp.wall_s,
                "reused_from": sp.reused_from,
                "ranges": [geo4326(g) for g in sp.ranges],
                "observed": geo4326(sp.observed),
                "validation": sp.validation,
            },
            "weather": ru["weather"],
            "llm_input": llm_in,
            "llm_model": agent.llm.model if agent is not None else None,
            "proposal": items,
            "agent_log": logs,
            "llm_calls": calls,
            "timing": {
                "rules_s": ru["seconds"],
                "llm_s": round(llm_s, 1),
                "round_s": round(time.time() - t_round, 1),
            },
        }
        (a.out / f"round_{rd.no}.json").write_text(
            json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8"
        )
        summary.append(
            {
                "round": rd.no,
                "at": rd.at,
                "adj": sp.calibration["adj"],
                "areas_ha": sp.areas_ha,
                "validation": sp.validation,
                "stage_now": ru["stage_now"],
                "stage_p5": ru["stage_p5"],
                "states": {i["id"]: i["state"] for i in items},
                "n_proposals": {i["id"]: len(i.get("proposals", [])) for i in items},
                "timing": out["timing"],
            }
        )
        print(
            f"  저장 round_{rd.no}/ · round_{rd.no}.json · 회차 {time.time() - t_round:.0f}초",
            flush=True,
        )
    (a.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n완료 {time.time() - t_all:.0f}초 → {a.out}", flush=True)


if __name__ == "__main__":
    main()
