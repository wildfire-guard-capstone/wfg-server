import json

import pytest
from results_example import make_run

from app.schemas.results import Incident, Perimeter, Prediction, Proposal
from app.services.results import FileResultsStore
from demo.uiseong.export_bundle import build, write


def test_bundle_matches_api_schemas(bundle_dir):
    store = FileResultsStore(bundle_dir)
    inc = Incident.model_validate(store.incident(1))
    assert inc.current_stage.value == "확산대응 2단계"  # 마지막 회차의 현재 단계
    assert inc.latest_proposal_version == "v2.1"
    lon, lat = inc.ignition_point["coordinates"]
    assert 128.5 < lon < 128.7 and 36.3 < lat < 36.45  # 5179 → 4326 변환
    assert lon == round(lon, 6)  # 좌표는 소수 6자리

    perims = [Perimeter.model_validate(p) for p in store.perimeters(1)]
    assert [p.version_no for p in perims] == [0, 1, 2]
    assert perims[0].geometry["type"] == "Point"
    assert perims[1].geometry["type"] == "MultiPolygon"  # Polygon도 MultiPolygon으로
    # 0.002° × 0.002° 사각형 ≈ 179 m × 222 m ≈ 3.98 ha (EPSG:5179 면적)
    assert perims[1].area_ha == pytest.approx(3.98, rel=0.05)

    pred = Prediction.model_validate(store.prediction(1, 1))
    assert [s.hour for s in pred.slices] == list(range(1, 9))  # P0(화선)은 빼고 P1~P8
    assert pred.areas_ha == [s.area_ha for s in pred.slices]
    assert pred.slices[4].at.isoformat() == "2025-03-22T17:50:00+09:00"  # P5 = 기준 + 5시간
    assert pred.validation[0].at.hour == 16

    prop = Proposal.model_validate(store.proposal(1, "v1.1"))
    assert prop.judged_stage.value == "확산대응 1단계"
    assert prop.predicted_stage.value == "확산대응 2단계"
    s1, e2 = prop.items
    assert (s1.code, s1.axis.value, [c.value for c in s1.categories]) == ("S1", "진화", ["협의"])
    assert e2.axis.value == "대피" and e2.state == "partial"
    assert s1.evidence[0].page == "79"  # 숫자 쪽 번호도 문자열로


def test_bundle_drops_prompts_and_logs(tmp_path):
    run, inp = make_run(tmp_path)
    files = build(run, inp)
    text = json.dumps(files, ensure_ascii=False)
    assert "agent_log" not in text and "llm_calls" not in text and "_prompt" not in text
    assert files["manifest.json"]["notice"].startswith("표준매뉴얼")


def test_unknown_stage_is_rejected(tmp_path):
    run, inp = make_run(tmp_path)
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    summary[0]["stage_p5"] = "3단계"
    (run / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="3단계"):
        build(run, inp)


def test_write_refuses_non_bundle_folder(tmp_path, bundle_dir):
    other = tmp_path / "other"
    other.mkdir()
    (other / "keep.txt").write_text("x")
    src = tmp_path / "src"
    src.mkdir()
    files = build(*make_run(src))
    with pytest.raises(SystemExit):
        write(files, other)  # 비어 있지 않음
    with pytest.raises(SystemExit):
        write(files, other, force=True)  # 묶음 폴더가 아니면 --force여도 지우지 않음
    assert (other / "keep.txt").exists()
    write(files, bundle_dir, force=True)  # 묶음 폴더는 --force로 다시 씀
    assert (bundle_dir / "manifest.json").exists()
