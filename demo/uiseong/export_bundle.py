"""데모 결과(round_*.json) → 결과 조회 API가 읽는 묶음 폴더(RESULTS_DIR).

    python -m demo.uiseong.export_bundle --run <결과 폴더> --input <demo-uiseong/input> \
        --out <묶음 폴더>

API(app.routers.incidents)는 이 폴더만 읽는다 — 프롬프트 · LLM 원문 · 실행 기록은 옮기지 않는다.
제안 근거에 표준매뉴얼 원문이 들어 있으므로 묶음 폴더는 저장소에 넣지 않고
공개 URL로 내보내지 않는다.

옮기는 것(회차 k = 1, 2, …):
  산불        scenario.json의 incident + perimeters.json 발화점(5179 → 4326)
  실측 화선    v0 = 발화점, vN = 회차 k의 기준 화선(spread.observed, N = 버전 vN.s의 N)
  예측 k      P1~P8(spread.ranges[1:]), 기상(시간별 바람), 보정 배율, 뒤 화선과의 비교
  제안서 vN.s  규칙 판정(현재 · P5 기준 단계, 지휘권자) + 항목별 LLM 제안 · 근거
"""

import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from shapely.geometry import Point, mapping, shape

from demo.uiseong.data import to4326, to5179

KST = timezone(timedelta(hours=9))
BUNDLE_FORMAT = 1
STAGES = {
    "초기대응": "초기대응",
    "1단계": "확산대응 1단계",
    "2단계": "확산대응 2단계",
    "확산대응 1단계": "확산대응 1단계",
    "확산대응 2단계": "확산대응 2단계",
}
AXES = {"S": "진화", "E": "대피"}
CATEGORIES = ("즉시", "대기", "협의", "요청")
NOTICE = "표준매뉴얼 원문 인용 포함 — 저장소 · 공개 URL에 올리지 말 것"


def _iso(text: str) -> str:
    """``2025-03-22 12:50`` · ``2025-03-22T12:50:00`` → ISO 8601(+09:00)."""
    return datetime.fromisoformat(text.replace(" ", "T")).replace(tzinfo=KST).isoformat()


def _round_coords(obj: Any, nd: int = 6) -> Any:
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, list | tuple):
        return [_round_coords(v, nd) for v in obj]
    return obj


def _geom(g: dict, *, multi: bool = False) -> dict:
    """좌표를 소수 6자리(약 0.1 m)로 줄이고, ``multi``면 Polygon을 MultiPolygon으로 감싼다."""
    if multi and g["type"] == "Polygon":
        g = {"type": "MultiPolygon", "coordinates": [g["coordinates"]]}
    return {"type": g["type"], "coordinates": _round_coords(g["coordinates"])}


def _area_ha(g: dict) -> float:
    return round(to5179(shape(g)).area / 10_000, 1)


def _stage(text: str) -> str:
    if text not in STAGES:
        raise ValueError(f"알 수 없는 대응단계: {text!r}")
    return STAGES[text]


def _version(text: str) -> tuple[int, int]:
    major, minor = text.lstrip("v").split(".")
    return int(major), int(minor)


def _item(it: dict) -> dict:
    cats: list[str] = []
    for sec in it.get("sections", []):
        for b in sec.get("blocks", []):
            c = b.get("category")
            if c in CATEGORIES and c not in cats:
                cats.append(c)
            elif c is not None and c not in CATEGORIES:
                raise ValueError(f"{it['id']}: 알 수 없는 분류 {c!r}")
    return {
        "code": it["id"],
        "axis": AXES[it["id"][0]],
        "name": it["name"],
        "state": it["state"],
        "finding": it.get("finding"),
        "categories": cats,
        "sections": it.get("sections", []),
        "evidence": [
            {
                "chunk_id": e["chunk_id"],
                "label": e["label"],
                "doc_name": e["doc_name"],
                "section": e.get("section"),
                "page": None if e.get("page") is None else str(e["page"]),
                "origin": e.get("origin"),
                "text": e["text"],
            }
            for e in it.get("evidence", [])
        ],
        "withheld": it.get("withheld", []),
    }


def build(run: Path, inp: Path, incident_id: int = 1) -> dict[str, Any]:
    """묶음 내용을 {상대 경로: JSON 값}으로 만든다(파일은 쓰지 않음)."""
    scenario = json.loads((inp / "scenario.json").read_text(encoding="utf-8"))
    perims = json.loads((inp / "perimeters.json").read_text(encoding="utf-8"))
    summary = {
        r["round"]: r for r in json.loads((run / "summary.json").read_text(encoding="utf-8"))
    }
    date = scenario["time"]["date"]
    inc = scenario["incident"]
    ign = perims["ignition"]
    ignition = _geom(mapping(to4326(Point(ign["x"], ign["y"]))))

    perimeters = {
        0: {
            "version_no": 0,
            "observed_at": _iso(inc["started_at"]),
            "report": "발화점",
            "area_ha": None,
            "geometry": ignition,
        }
    }
    predictions, proposals = [], []
    for k in sorted(summary):
        r = json.loads((run / f"round_{k}.json").read_text(encoding="utf-8"))
        meta, sp, common = r["round"], r["spread"], r["llm_input"]["common"]
        pv, seq = _version(meta["version"])
        base = datetime.fromisoformat(meta["base_at"]).replace(tzinfo=KST)
        perimeters.setdefault(
            pv,
            {
                "version_no": pv,
                "observed_at": base.isoformat(),
                "report": meta.get("report"),
                "area_ha": _area_ha(sp["observed"]),
                "geometry": _geom(sp["observed"], multi=True),
            },
        )
        cal = r.get("calibration")
        wind_ms, wind_deg = r["weather"]["wind_ms"], r["weather"]["wind_deg"]
        predictions.append(
            {
                "id": k,
                "perimeter_version": pv,
                "base_at": base.isoformat(),
                "areas_ha": sp["areas_ha"][1:],
                "calibration": cal
                and {"method": cal["method"], "adj": round(cal["adj"], 3), "basis": cal["from"]},
                "weather": [
                    {"hour": int(h), "wind_ms": wind_ms[h], "wind_deg": wind_deg[h]}
                    for h in sorted(wind_ms, key=int)
                ],
                "slices": [
                    {
                        "hour": n,
                        "at": (base + timedelta(hours=n)).isoformat(),
                        "area_ha": sp["areas_ha"][n],
                        "geometry": _geom(sp["ranges"][n], multi=True),
                    }
                    for n in range(1, len(sp["ranges"]))
                ],
                "validation": [
                    {**v, "at": _iso(f"{date} {v['at']}")} for v in sp.get("validation", [])
                ],
            }
        )
        items = [_item(it) for it in r["proposal"]]
        proposals.append(
            {
                "version": meta["version"],
                "perimeter_version": pv,
                "seq": seq,
                "prediction_id": k,
                "trigger": common["proposal"]["trigger"],
                "reader": meta["reader"],
                "commander": common["commander"],
                "judged_stage": _stage(common["current_stage"]),
                "predicted_stage": _stage(summary[k]["stage_p5"]),
                "base_at": base.isoformat(),
                "created_at": _iso(common["proposal"]["generated_at"]),
                "item_states": {i["code"]: i["state"] for i in items},
                "items": items,
            }
        )

    latest = proposals[-1] if proposals else None
    incident = {
        "id": incident_id,
        "name": inc["name"],
        "address": inc["address"],
        "sigungu": inc["sigungu"],
        "emd": inc["emd"],
        "status": inc["status"],
        "current_stage": latest["judged_stage"] if latest else "초기대응",
        "started_at": _iso(inc["started_at"]) if inc.get("started_at") else None,
        "reported_at": _iso(inc["reported_at"]),
        "latest_perimeter_version": max(perimeters),
        "latest_proposal_version": latest["version"] if latest else None,
        "ignition_point": ignition,
    }
    d = f"incidents/{incident_id}"
    out: dict[str, Any] = {
        "manifest.json": {
            "format": BUNDLE_FORMAT,
            "exported_at": datetime.now(KST).isoformat(timespec="seconds"),
            "source": {"run": run.name, "input": inp.name},
            "notice": NOTICE,
            "incidents": [incident_id],
        },
        f"{d}/incident.json": incident,
        f"{d}/perimeters.json": [perimeters[v] for v in sorted(perimeters)],
    }
    for p in predictions:
        out[f"{d}/predictions/{p['id']}.json"] = p
    for p in proposals:
        out[f"{d}/proposals/{p['version']}.json"] = p
    return out


def write(files: dict[str, Any], out: Path, *, force: bool = False) -> None:
    if out.exists() and any(out.iterdir()):
        if not force:
            raise SystemExit(f"{out}가 비어 있지 않습니다. 덮어쓰려면 --force")
        if not (out / "manifest.json").exists():
            raise SystemExit(f"{out}는 묶음 폴더가 아닙니다(manifest.json 없음). 지우지 않습니다")
        shutil.rmtree(out)
    for rel, value in files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m demo.uiseong.export_bundle")
    ap.add_argument(
        "--run", type=Path, required=True, help="결과 폴더(summary.json · round_k.json)"
    )
    ap.add_argument("--input", type=Path, required=True, help="demo-uiseong/input")
    ap.add_argument("--out", type=Path, required=True, help="묶음 폴더(RESULTS_DIR)")
    ap.add_argument("--incident-id", type=int, default=1)
    ap.add_argument("--force", action="store_true", help="기존 묶음 폴더를 지우고 다시 씀")
    a = ap.parse_args(argv)
    files = build(a.run, a.input, a.incident_id)
    write(files, a.out, force=a.force)
    size = sum((a.out / rel).stat().st_size for rel in files)
    print(f"{len(files)}개 파일 · {size / 1_048_576:.1f} MB → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
