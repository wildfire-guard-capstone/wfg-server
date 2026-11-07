"""의성 데모 실행(GPU 서버): 회차마다 ELMFIRE 보정 · 예측 → 규칙 엔진 → 에이전트(EXAONE) → 저장.

    python -m demo.uiseong.run_demo --data <데모 데이터> --deck <ELMFIRE 입력>
        --elmfire <실행 파일> --out <결과>
        [--rounds 1,2,3,4] [--skip-llm] [--llm http://127.0.0.1:8031] [--evidence http://127.0.0.1:8032]

회차 k(11/7 사용자 결정):
  1. 보정: 직전 상태 → 이번 화선의 면적이 맞게 확산속도 배율(adj) 하나를 찾는다
     (1회차 = 11:24 발화점 → 12:50 1보, k회차 = k−1보 화선 → k보 화선)
  2. 예측: k보 화선에서 그 배율로 8시간 → P0(관측 화선) ~ P8
  3. 검증: 8시간 안에 들어오는 뒤 상황도 화선과 면적 · 겹침(IoU) 비교(제안에는 쓰지 않음)
  4. 규칙 엔진: build_llm_input(Inputs) — 직전 회차의 현재 단계 · 읍면 Set/Go를 이어 받는다
  5. 에이전트: 항목별 문단 생성 · 검사 · 조립
결과: <out>/round_<k>.json(전부) + <out>/summary.json
"""

import argparse
import json
import math
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
from shapely.geometry import mapping
from shapely.ops import unary_union

from agent.clients import LLM, Evidence
from agent.generate import Agent
from demo.uiseong.data import MapData, lonlat, to4326
from demo.uiseong.rounds import INCIDENT, ROUNDS
from rules.context import Inputs
from rules.core.stage import LABEL
from rules.llm_input import build_llm_input
from spread import perimeter_run as pr

IGNITION_T_S = 1440.0  # 11:24
MPH = 0.44704
STAGE_OF = {v: k for k, v in LABEL.items()}


def wind_series(deck: Path, at_xy, t0_s: float, hours: int = 8) -> list[tuple[float, float]]:
    """ELMFIRE 입력의 시간별 풍속(mph) · 풍향을 한 점에서 읽어
    t0 + h시간으로 선형 보간(u · v 성분)."""
    import rasterio

    def band_vals(name):
        with rasterio.open(deck / "inputs" / f"{name}.tif") as r:
            row, col = r.index(*at_xy)
            return r.read()[:, row, col].astype(float)

    ws, wd = band_vals("ws") * MPH, band_vals("wd")
    u = -ws * np.sin(np.radians(wd))
    v = -ws * np.cos(np.radians(wd))
    out = []
    for h in range(hours + 1):
        x = (t0_s + h * 3600) / 3600.0  # 밴드 1 = 0시간
        i = min(int(math.floor(x)), len(ws) - 2)
        f = x - i
        uu, vv = u[i] * (1 - f) + u[i + 1] * f, v[i] * (1 - f) + v[i + 1] * f
        out.append((math.hypot(uu, vv), (math.degrees(math.atan2(-uu, -vv)) + 360) % 360))
    return out


def geo4326(g) -> dict:
    return mapping(to4326(g))


def known_names(md: MapData) -> set[str]:
    names = {v.name for v in md.villages}
    for group in (
        md.vulnerable,
        md.shelters,
        md.heritage,
        md.police,
        md.fire_stations,
        md.hospitals,
    ):
        names |= {p.name for p in group}
    names |= {b.name for b in md.bases} | {r.name for r in md.sigungu}
    return {n for n in names if len(n) >= 2}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--deck", type=Path, required=True)
    ap.add_argument("--elmfire", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rounds", default="1,2,3,4")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument(
        "--spread-only", action="store_true", help="보정 · 예측 · 검증만(규칙 엔진 · 에이전트 없음)"
    )
    ap.add_argument("--llm", default="http://127.0.0.1:8031")
    ap.add_argument("--evidence", default="http://127.0.0.1:8032")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    work = a.out / "_elmfire"
    md = MapData(a.data)
    deck = pr.Deck(a.deck, a.elmfire, env={})
    agent = None
    if not a.skip_llm:
        agent = Agent(LLM(a.llm), Evidence(a.evidence), known_names(md), workers=a.workers)
    t_all = time.time()
    print(
        f"지도 자료: 마을 {len(md.villages)} · 주택 {len(md.houses[0])} · "
        f"대피소 {len(md.shelters)} · "
        f"취약시설 {len(md.vulnerable)} · 인접 시군 {md.neighbors['의성군']}",
        flush=True,
    )
    wanted = {int(x) for x in a.rounds.split(",")}
    prev_stage, prev_alerts, prev_paras, summary = None, (), {}, []
    for rd in ROUNDS:
        if rd.no not in wanted:
            continue
        t_round = time.time()
        print(f"\n=== 회차 {rd.no} {rd.version} {rd.at} ({rd.report}, {rd.reader}) ===", flush=True)
        observed = md.report(rd.at)
        # 1. 보정
        if rd.no == 1:
            # 발화 뒤 성장 속도 유지(11/7 사용자 결정): 1보 화선에서 1시간 동안 늘어나는 면적
            # = 발화(11:24) ~ 1보(12:50) 평균 속도. 12:50에 아는 정보만 쓴다
            rate = observed.area / 1e4 / ((rd.t_s - IGNITION_T_S) / 3600)
            target = observed.area / 1e4 + rate
            cal = pr.calibrate(
                deck, work / f"cal_{rd.no}", observed, rd.t_s, rd.t_s + 3600, None, target_ha=target
            )
            cal_from = f"발화 뒤 평균 성장 속도 유지(시간당 {rate:.1f} ha, 1보 화선 + 1시간)"
        else:
            before = ROUNDS[rd.no - 2]
            start, t_from = md.report(before.at), before.t_s
            cal = pr.calibrate(deck, work / f"cal_{rd.no}", start, t_from, rd.t_s, observed)
            cal_from = f"{before.report} {before.at} → {rd.report} {rd.at} 화선"
        print(
            f"  보정 adj {cal.adj:.3f} · 목표 {cal.target_ha:.1f} ha · 결과 {cal.got_ha:.1f} ha · "
            f"시도 {len(cal.tries)}회{' · 범위 끝(' + cal.clipped + ')' if cal.clipped else ''}",
            flush=True,
        )
        # 2. 예측
        res = pr.run(deck, work / f"fcst_{rd.no}", rd.t_s, 8, observed, cal.adj)
        rngs = pr.ranges(res, observed)
        areas = [round(g.area / 1e4, 1) for g in rngs]
        print(f"  예측 {res.wall_s:.1f}초 · P0~P8 {areas} ha", flush=True)
        # 3. 검증(뒤 상황도)
        checks = []
        for later in md.perimeters["reports"] + md.perimeters["later"]:
            h, m = map(int, later["at"].split(":"))
            dt_h = (h * 60 + m - (rd.base_at.hour * 60 + rd.base_at.minute)) / 60
            if 0 < dt_h <= 8:
                pred = pr.polygonize(res.burned_by(rd.t_s + dt_h * 3600), res.transform)
                pred = unary_union([pred, observed])
                obs = unary_union([md.report(later["at"]), observed])
                inter = pred.intersection(obs).area
                checks.append(
                    {
                        "at": later["at"],
                        "hours": round(dt_h, 2),
                        "pred_ha": round(pred.area / 1e4, 1),
                        "obs_ha": round(obs.area / 1e4, 1),
                        "iou": round(inter / pred.union(obs).area, 3) if pred.area else 0,
                    }
                )
        print(
            f"  검증 {[(c['at'], c['pred_ha'], c['obs_ha'], c['iou']) for c in checks]}", flush=True
        )
        if a.spread_only:
            summary.append(
                {
                    "round": rd.no,
                    "at": rd.at,
                    "adj": cal.adj,
                    "areas_ha": areas,
                    "validation": checks,
                }
            )
            continue
        # 4. 규칙 엔진
        winds = wind_series(a.deck, (observed.centroid.x, observed.centroid.y), rd.t_s)
        wind_ms = {0: rd.wind_ms, **{h: round(w[0], 1) for h, w in enumerate(winds) if h > 0}}
        wind_deg = {0: rd.wind_dir_deg, **{h: round(w[1]) for h, w in enumerate(winds) if h > 0}}
        field = {v.id: rd.field_by_name[v.name] for v in md.villages if v.name in rd.field_by_name}
        lon, lat = lonlat(md.ignition)
        inputs = Inputs(
            base_at=rd.base_at,
            generated_at=rd.base_at,
            reader=rd.reader,
            prev_stage=prev_stage,
            ranges=rngs,
            ignition=md.ignition,
            wind_ms=wind_ms,
            wind_deg=wind_deg,
            houses=md.houses,
            sido=md.sido,
            sigungu=md.sigungu,
            villages=md.villages,
            site=(lat, lon),
            field=field,
            temp_c=rd.temp_c,
            rh_pct=rd.rh_pct,
            heritage=md.heritage,
            temples=(),
            substations=md.substations,
            power_lines=md.power_lines,
            vulnerable=md.vulnerable,
            shelters=md.shelters,
            roads=md.roads,
            junctions=(),
            police=md.police,
            fire_stations=md.fire_stations,
            hospitals=md.hospitals,
            bases=md.bases,
            deployed=rd.deployed,
            neighbors=md.neighbors,
            forest_dept={"의성군": "의성군 산림녹지과"},
            prev_alerts=prev_alerts,
        )
        t_rule = time.time()
        llm_in = build_llm_input(inputs, INCIDENT)
        rule_s = time.time() - t_rule
        llm_in["common"]["proposal"] |= {
            "version": rd.version,
            "trigger": "예측 갱신",
            "base_perimeter": f"{rd.report} ({rd.at}, 상황도)",
        }
        b = llm_in["bundles"]
        print(
            f"  규칙 엔진 {rule_s:.1f}초 · 현재 {b['stage']['now']['stage']} · "
            f"P5 {b['stage']['p5']['stage']} · "
            f"지휘권자 {b['commander']['now']['title']} → {b['commander']['next']['title']} · "
            f"항목 {[i['id'] for i in llm_in['items']]}",
            flush=True,
        )
        # 5. 에이전트
        items, logs, llm_s = None, [], 0.0
        if agent is not None:
            n0 = len(agent.llm.calls)
            t_llm = time.time()
            items, logs = agent.run(llm_in["common"], llm_in["items"], prev_paras)
            llm_s = time.time() - t_llm
            states = {i["id"]: i["state"] for i in items}
            print(
                f"  에이전트 {llm_s:.0f}초 · 호출 {len(agent.llm.calls) - n0}회 · {states}",
                flush=True,
            )
            prev_paras = {
                i["id"]: {
                    "input_key": i["input_key"],
                    "paras": i.get("paras"),
                    "state": i["state"],
                    "version": rd.version,
                }
                for i in items
                if i.get("paras")
            }
            calls = agent.llm.calls[n0:]
        else:
            calls = []
        prev_stage = STAGE_OF[b["stage"]["now"]["stage"]]
        prev_alerts = tuple((r["sigungu"], r["emd"], r["level"]) for r in b["alerts"]["areas"])
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
            "calibration": {
                "adj": cal.adj,
                "target_ha": round(cal.target_ha, 1),
                "got_ha": round(cal.got_ha, 1),
                "from": cal_from,
                "tries": [(round(k, 3), round(v, 1)) for k, v in cal.tries],
                "clipped": cal.clipped,
            },
            "spread": {
                "areas_ha": areas,
                "wall_s": round(res.wall_s, 1),
                "ranges": [geo4326(g) for g in rngs],
                "observed": geo4326(observed),
                "validation": checks,
            },
            "weather": {"wind_ms": wind_ms, "wind_deg": wind_deg},
            "llm_input": llm_in,
            "proposal": items,
            "agent_log": logs,
            "llm_calls": calls,
            "timing": {
                "rules_s": round(rule_s, 2),
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
                "adj": cal.adj,
                "areas_ha": areas,
                "validation": checks,
                "stage_now": b["stage"]["now"]["stage"],
                "stage_p5": b["stage"]["p5"]["stage"],
                "states": {i["id"]: i["state"] for i in (items or [])},
                "timing": out["timing"],
            }
        )
        print(f"  저장 round_{rd.no}.json · 회차 {time.time() - t_round:.0f}초", flush=True)
    (a.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n완료 {time.time() - t_all:.0f}초 → {a.out}", flush=True)


if __name__ == "__main__":
    main()
