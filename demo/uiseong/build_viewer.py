# ruff: noqa: E501
"""데모 결과(round_*.json) → 한 장짜리 뷰어 HTML(로컬에서 연다).

    python -m demo.uiseong.build_viewer --runs <결과 폴더> --data <demo-uiseong/input> --out <viewer.html>

지도(Leaflet · OpenStreetMap 타일): P1 ~ P8 예측 범위, P0(관측 화선),
8시간 안 뒤 상황도 화선(검증, 점선),
범위에 든 마을(구간 색), 취약시설. 오른쪽: 회차 제안서(12개 항목). 탭: 예측 · 검증, 실행 기록.
"""

import argparse
import html
import json
import re
from pathlib import Path

from shapely.geometry import mapping, shape

from demo.uiseong.data import to4326

ZONE_KEYS = ("burning", "p5", "p8")


def model_label(rounds: list[dict]) -> str:
    """머리말의 LLM 이름. run_demo가 round_k.json에 남긴 llm_model(11/9부터)을 읽는다.
    예: exaone-4.5-33b-awq → EXAONE 4.5 33B. 기록이 없는 옛 실행(tune15까지 = 4.0 32B)은 「모델 기록 없음」."""
    ids = list(dict.fromkeys(r.get("llm_model") for r in rounds if r.get("llm_model")))
    if not ids:
        if any(r["proposal"] and r["proposal"][0].get("state") != "not_run" for r in rounds):
            return "EXAONE(모델 기록 없음)"
        return "LLM 생성 없음"
    out = []
    for m in ids:
        g = re.match(r"exaone-(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)b", m, re.I)
        out.append(f"EXAONE {g[1]} {g[2].upper()}B" if g else m)
    return " · ".join(out)


def _round(path: Path) -> dict:
    r = json.loads(path.read_text(encoding="utf-8"))
    b = r["llm_input"]["bundles"]
    villages = [
        {
            "id": v["id"],
            "name": v["name"],
            "emd": v["emd"],
            "zone": z,
            "until": v["until"],
            "from": v["from"],
            "people": v["population"],
            "night": v["night"],
        }
        for z in ZONE_KEYS
        for v in b["villages"][z]
    ]
    vul = [
        {k: f[k] for k in ("id", "name", "kind", "village", "zone", "until")}
        for f in b["vulnerable"].get("in_range", [])
    ]
    proposal = r["proposal"] or [
        {
            **{k: it.get(k) for k in ("id", "name", "status", "finding", "withheld")},
            "state": "not_run",
            "sections": it["sections"],
        }
        for it in r["llm_input"]["items"]
    ]
    for it in proposal:
        it.pop("input_key", None)
        it.pop("paras", None)
    logs = []
    for lg in r.get("agent_log") or []:
        logs.append(
            {
                "id": lg["id"],
                "reused_from": lg.get("reused_from"),
                "evidence": lg.get("evidence"),
                "queries": (lg.get("retrieval") or {}).get("queries"),
                "dropped": lg.get("dropped"),
                "attempts": [{"errors": a["errors"]} for a in lg.get("attempts", [])],
            }
        )
    return {
        "round": r["round"],
        "calibration": r["calibration"],
        "spread": r["spread"],
        "weather": r["weather"],
        "common": r["llm_input"]["common"],
        "stage": b["stage"],
        "commander": b["commander"],
        "area": b["area"],
        "villages": villages,
        "vulnerable": vul,
        "proposal": proposal,
        "logs": logs,
        "calls": r.get("llm_calls") or [],
        "timing": r["timing"],
        "llm_model": r.get("llm_model"),
    }


def build(runs: Path, data: Path) -> dict:
    rounds = [_round(p) for p in sorted(runs.glob("round_*.json"))]
    ids = {v["id"] for r in rounds for v in r["villages"]}
    vfeats = []
    for f in json.loads(next((data / "map").glob("03_*.geojson")).read_text(encoding="utf-8"))[
        "features"
    ]:
        if int(f["properties"]["code"]) in ids:
            g = shape(f["geometry"]).simplify(0.0002, preserve_topology=True)
            vfeats.append({"id": int(f["properties"]["code"]), "geom": mapping(g)})
    regions = json.loads((data / "regions.json").read_text(encoding="utf-8"))
    for t in regions.get("townships", []):  # 의성 밖 읍면 단위 마을(EPSG:5179)
        if int(t["code"]) in ids:
            g = to4326(shape(t["geom"])).simplify(0.0002, preserve_topology=True)
            vfeats.append({"id": int(t["code"]), "geom": mapping(g)})
    vul_pts = {}
    for i, f in enumerate(
        json.loads(next((data / "map").glob("07_*.geojson")).read_text(encoding="utf-8"))[
            "features"
        ],
        1,
    ):
        vul_pts[1000 + i] = f["geometry"]["coordinates"]
    per = json.loads((data / "perimeters.json").read_text(encoding="utf-8"))
    later = [
        {"at": p["at"], "no": p["no"], "geom": mapping(to4326(shape(p["geom"]).buffer(0)))}
        for p in per["reports"] + per["later"]
    ]
    ig = to4326(
        shape({"type": "Point", "coordinates": [per["ignition"]["x"], per["ignition"]["y"]]})
    )
    return {
        "rounds": rounds,
        "village_geoms": vfeats,
        "vul_pts": vul_pts,
        "perimeters": later,
        "ignition": [ig.y, ig.x],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    payload = build(a.runs, a.data)
    tpl = (Path(__file__).parent / "viewer_template.html").read_text(encoding="utf-8")
    page = tpl.replace("__MODEL__", html.escape(model_label(payload["rounds"]))).replace(
        "/*__DATA__*/null", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    a.out.write_text(page, encoding="utf-8")
    print(f"회차 {len(payload['rounds'])}개 → {a.out} ({a.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
