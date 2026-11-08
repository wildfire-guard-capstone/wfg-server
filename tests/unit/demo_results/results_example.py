"""(김지오 전용 · 임시) 결과 조회 API 테스트용 — 작은 가짜 데모 결과(2회차).

묶음 폴더 fixture는 conftest.py.
"""

import json
from pathlib import Path

# 의성 안평면 근처(경도, 위도)
LON, LAT = 128.6, 36.37


def _square(d: float) -> dict:
    ring = [
        [LON - d, LAT - d],
        [LON + d, LAT - d],
        [LON + d, LAT + d],
        [LON - d, LAT + d],
        [LON - d, LAT - d],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def _item(code: str, state: str, category: str) -> dict:
    return {
        "id": code,
        "name": f"{code} 항목",
        "state": state,
        "finding": f"{code} 판정",
        "withheld": [{"field": "진화예상시간", "reason": "MVP에서 제외"}],
        "sections": [
            {
                "title": "대응 제안",
                "blocks": [
                    {
                        "type": "paragraph",
                        "category": category,
                        "text": f"{code} 제안 문장을 실행하십시오 [p.79].",
                        "evidence": ["SM-p079-s01"],
                        "quote": "근거 문장",
                        "source": "llm",
                    }
                ],
            }
        ],
        "evidence": [
            {
                "chunk_id": "SM-p079-s01",
                "label": "p.79",
                "doc_name": "표준매뉴얼",
                "section": "p.79",
                "page": 79,
                "origin": "검색",
                "text": "근거 원문",
            }
        ],
        "proposals": [],
    }


def make_run(tmp: Path) -> tuple[Path, Path]:
    run, inp = tmp / "run", tmp / "input"
    run.mkdir()
    inp.mkdir()
    (inp / "scenario.json").write_text(
        json.dumps(
            {
                "time": {"date": "2025-03-22"},
                "incident": {
                    "name": "가짜 산불",
                    "sigungu": "의성군",
                    "emd": "안평면",
                    "address": "경북 의성군 안평면",
                    "started_at": "2025-03-22 11:24",
                    "reported_at": "2025-03-22 11:25",
                    "status": "진행 중",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (inp / "perimeters.json").write_text(
        json.dumps({"ignition": {"x": 1098492.2, "y": 1819712.0, "at": "11:24"}}),
        encoding="utf-8",
    )
    summary = []
    for k, (at, stage_now, stage_p5, reader) in enumerate(
        [("12:50", "1단계", "2단계", "의성군수"), ("13:10", "2단계", "2단계", "경상북도지사")],
        start=1,
    ):
        states = {"S1": "ok", "E2": "partial"}
        summary.append(
            {"round": k, "at": at, "stage_now": stage_now, "stage_p5": stage_p5, "states": states}
        )
        r = {
            "round": {
                "no": k,
                "version": f"v{k}.1",
                "at": at,
                "report": f"{k}보",
                "reader": reader,
                "base_at": f"2025-03-22T{at}:00",
            },
            "calibration": {
                "method": "rate",
                "adj": 2.75062,
                "from": "성장 속도 유지",
                "parts": [],
            },
            "spread": {
                "areas_ha": [10.0 * (n + 1) for n in range(9)],
                "ranges": [_square(0.001 * (n + 1)) for n in range(9)],
                "observed": _square(0.001),
                "validation": [
                    {"at": "16:30", "hours": 3.0, "pred_ha": 40.0, "obs_ha": 50.0, "iou": 0.5}
                ],
            },
            "weather": {
                "wind_ms": {str(h): 3.0 + h for h in range(9)},
                "wind_deg": {str(h): 270.0 for h in range(9)},
            },
            "llm_input": {
                "common": {
                    "current_stage": f"확산대응 {stage_now}",
                    "commander": {"now": reader, "next": "경상북도지사"},
                    "proposal": {
                        "trigger": "예측 갱신",
                        "generated_at": f"2025-03-22 {at}",
                        "base_at": f"2025-03-22 {at}",
                        "version": f"v{k}.1",
                    },
                }
            },
            "proposal": [_item("S1", "ok", "협의"), _item("E2", "partial", "즉시")],
        }
        (run / f"round_{k}.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    (run / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return run, inp
