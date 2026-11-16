# ruff: noqa: E501
"""제안서 하네스: 저장된 입력 재생(가짜 · 에이전트 작성기) · 버그 검사(B01 ~ B07) · CSV · 비교.

검사 예시 문장은 2026-11-16 제안서(proposal_items 11개)에서 실제로 나온 꼴을 따랐다.
"""

import csv
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from rules_example import full_inputs

from app.propose.agent import AgentWriter
from app.propose.fake import FakeWriter
from evaluation.proposals import checks, harness
from rules.llm_input import build_items, build_llm_input

INCIDENT = {"name": "의성군 안평면 산불 (2025-03-22)", "sigungu": "의성군", "emd": "안평면"}


def roundtrip(x):
    """DB JSONB에 넣었다 꺼낸 모양."""
    return json.loads(json.dumps(x, ensure_ascii=False))


@pytest.fixture(scope="module")
def engine():
    return build_llm_input(full_inputs(), INCIDENT)


def row(code, *bodies, facts=None, state="ok", proposal_id=1, run=0, **para):
    return {
        "run": run,
        "proposal_id": proposal_id,
        "item_code": code,
        "state": state,
        "detail": {"id": code, "facts": facts or {}},
        "sections": [
            {"title": "1.", "blocks": [{"type": "paragraph", "body": b, **para} for b in bodies]}
        ],
        "evidence": [],
    }


def bugs(*rows):
    return [(f["bug"], f["item_code"]) for f in checks.find(rows)]


# ── 입력 재생 ──────────────────────────────────────────────────────────────


def test_parse_ids():
    assert harness.parse_ids("1-3,7") == [1, 2, 3, 7]
    assert harness.parse_ids(" 2, 2,1 ") == [2, 1]
    for bad in ("", "3-1", "a"):
        with pytest.raises(ValueError):
            harness.parse_ids(bad)


def test_stored_input_replays_as_engine_input(engine):
    """DB에 저장된 묶음 · 항목으로 다시 만든 입력 = 그때 작성기가 받은 입력."""
    bundles, items = roundtrip(engine["bundles"]), roundtrip(engine["items"])
    assert harness.replay_input(bundles, items, INCIDENT) == roundtrip(engine)
    assert (
        roundtrip(build_items(bundles)) == items
    )  # --rebuild-items: 저장된 묶음으로 항목을 다시 만들어도 같음


def test_load_inputs_from_db(engine, monkeypatch):
    """DB에서: 항목은 S1 ~ E6 순서로, 산불은 만들 때처럼 「진행 중」으로(지금 종료여도)."""
    from app.db.models import Proposal
    from app.repositories import engine_inputs, proposals

    bundles, items = roundtrip(engine["bundles"]), roundtrip(engine["items"])

    class DB:
        def get(self, model, pk):
            if model is not Proposal:
                return SimpleNamespace(id=pk)
            return SimpleNamespace(id=pk, incident_id=7, context=bundles) if pk == 5 else None

    stored = [SimpleNamespace(item_code=it["id"], detail=it) for it in reversed(items)]
    monkeypatch.setattr(proposals, "items_of", lambda db, pid: stored)
    monkeypatch.setattr(engine_inputs, "incident_info", lambda db, i: INCIDENT | {"status": "종료"})
    case = harness.load_inputs(DB(), [5])[5]
    assert case["incident_id"] == 7
    assert case["input"]["items"] == items
    assert case["input"]["common"]["incident"] == INCIDENT | {"status": "진행 중"}
    rebuilt = harness.load_inputs(DB(), [5], rebuild_items=True)[5]
    assert roundtrip(rebuilt["input"]["items"]) == items
    with pytest.raises(SystemExit):
        harness.load_inputs(DB(), [6])


def test_distinct_keeps_first_of_same_input(engine):
    case = {"incident_id": 1, "input": engine}
    other = {"incident_id": 1, "input": engine | {"items": engine["items"][:1]}}
    kept, same = harness.distinct({2: case, 4: other, 6: roundtrip(case)})
    assert list(kept) == [2, 4]
    assert same == {6: 2}


def test_replay_writes_rows_and_resumes(engine, tmp_path):
    inputs = {1: {"incident_id": 9, "input": engine}}
    rows = harness.replay(inputs, FakeWriter(), tmp_path, repeat=2, say=lambda _: None)
    n = len(engine["items"])
    assert len(rows) == 2 * n
    assert {(r["run"], r["proposal_id"], r["incident_id"]) for r in rows} == {(1, 1, 9), (2, 1, 9)}
    first = rows[0]
    assert first["item_code"] == engine["items"][0]["id"]
    assert first["detail"] == roundtrip(engine["items"][0])
    assert first["text"] == "\n".join(p["body"] for p in checks.paragraphs(first))
    assert checks.find(rows) == []  # 가짜 작성기 문장에는 버그가 없다

    said = []
    assert len(harness.replay(inputs, FakeWriter(), tmp_path, repeat=2, say=said.append)) == 2 * n
    assert said == ["이미 끝난 출력 2개는 건너뜁니다"]  # 끝난 것은 다시 돌리지 않음
    assert len(harness.replay(inputs, FakeWriter(), tmp_path, repeat=3, say=said.append)) == 3 * n


class FakeEvidence:
    CHUNK = {
        "chunk_id": "SM-p075-s01",
        "doc_id": "SM",
        "doc_name": "표준매뉴얼",
        "page": "75",
        "section": "p.75",
        "text": "위험구역 주민은 바로 대피시킨다",
    }

    def fetch(self, ids):
        return [], list(ids)

    def search(self, query, k=2, exclude=()):
        return [self.CHUNK | {"score": 0.7}]


class EchoLLM:
    def chat(self, messages, schema=None, **kw):
        props = schema["properties"]["proposals"]["items"]["properties"]
        proposal = {
            "topic": props["topic"]["enum"][0],
            "category": "즉시",
            "text": "주민을 대피시키십시오.",
            "evidence": [props["evidence"]["items"]["enum"][0]],
            "quote": "위험구역 주민은 바로 대피",
        }
        return json.dumps({"proposals": [proposal]}, ensure_ascii=False)


def test_replay_agent_writer_keeps_reasons(engine, tmp_path):
    """API와 같은 AgentWriter를 부르고, 에이전트 기록에서 이유만 logs.jsonl에 남긴다."""
    writer = AgentWriter(EchoLLM(), FakeEvidence(), workers=1)
    inputs = {3: {"incident_id": 1, "input": engine}}
    rows = harness.replay(inputs, writer, tmp_path, say=lambda _: None)
    logs = harness.read_jsonl(tmp_path / harness.LOGS)
    assert [g["item_code"] for g in logs] == [r["item_code"] for r in rows]
    assert {g["run"] for g in logs} == {1} and {g["proposal_id"] for g in logs} == {3}
    assert set(logs[0]) == {  # 보낸 프롬프트 · 받은 답 원문(근거 원문이 들어 있음)은 남기지 않는다
        "run",
        "proposal_id",
        "item_code",
        "input_key",
        "errors",
        "dropped",
        "evidence",
        "situations",
    }
    assert {r["input_key"] for r in rows} == {g["input_key"] for g in logs}


def test_meta_must_match_to_resume(tmp_path):
    meta = {"proposals": [1, 2], "writer": "agent", "rebuild_items": False, "label": "before"}
    harness.check_meta(tmp_path, meta)
    harness.check_meta(tmp_path, meta | {"label": "다시"})  # 이름 · 시각은 달라도 됨
    with pytest.raises(SystemExit):
        harness.check_meta(tmp_path, meta | {"proposals": [1]})


# ── 버그 검사 ──────────────────────────────────────────────────────────────


def test_b01_stage_threshold_and_time_order():
    bad = "피해면적이 17:50에 101.8 ha, 16:50에 70.7 ha로 확산대응 1단계 기준 100 ha를 넘을 수 있습니다."
    good = "피해면적이 13:54에 11.2 ha, 16:54에 27.8 ha로 확산대응 1단계 기준 10 ha를 넘을 수 있습니다."
    midnight = (
        "피해면적이 23:30에 11 ha, 01:30에 120 ha로 확산대응 2단계 기준 100 ha를 넘을 수 있습니다."
    )
    found = [f["detail"] for f in checks.find([row("S1", bad)])]
    assert len(found) == 2
    assert "1단계 기준 100" in found[0] and "17:50 → 16:50" in found[1]
    assert bugs(row("S1", good), row("S1", midnight), row("S2", bad)) == []


def test_b02_stage_that_does_not_exist():
    assert bugs(row("S1", "대응단계가 2단계에서 격상될 경우 산림청장과 협의하십시오.")) == [
        ("B02", "S1")
    ]
    assert bugs(row("S1", "확산대응 3단계 발령을 검토하십시오.")) == [("B02", "S1")]
    now2 = {"now": {"stage": "2단계"}}
    assert bugs(row("S1", "향후 단계 격상 시 산림청장과 협의하십시오.", facts=now2)) == [
        ("B02", "S1")
    ]
    ok = row(
        "S1",
        "기준에 가까워지면 확산대응 1단계 격상을 협의하십시오.",
        facts={"now": {"stage": "초기 대응"}},
    )
    assert bugs(ok) == []


def test_b03_strong_wind_premise_without_rising_wind():
    body = "강풍이 풀리면 헬기를 바로 다시 투입할 수 있도록 이륙 준비 현황과 격납고 지역 풍속을 확인하십시오."
    calm = {"now": {"wind_ms": 1.5}, "max": {"ms": 1.8}, "sun": {"now_is_night": False}}
    rising = {"now": {"wind_ms": 2.7}, "max": {"ms": 6.5}, "sun": {"now_is_night": False}}
    assert bugs(row("S4", body, facts=calm)) == [("B03", "S4")]
    assert bugs(row("S4", body, facts=rising)) == []


def test_b04_aerial_orders_at_night():
    night = {"now": {"wind_ms": 0.3}, "max": {"ms": 1.1}, "sun": {"now_is_night": True}}
    day = night | {"sun": {"now_is_night": False}}
    s3 = "가용 헬기를 집중 투입하고 확산에 따라 단계적으로 늘리십시오."
    s4 = "바람이 세지 않으므로 공중·지상 진화를 함께 이어가십시오. 일출 뒤 공중 진화를 다시 시작하십시오."
    at_night = [row("S3", s3), row("S4", s4, facts=night)]
    assert sorted(bugs(*at_night)) == [("B04", "S3"), ("B04", "S4")]
    assert bugs(row("S3", s3), row("S4", s4, facts=day)) == []
    other = row("S3", s3, proposal_id=2)  # 밤인지는 같은 제안서의 S4로 본다
    assert bugs(other, row("S4", "지상 인력을 유지하십시오.", facts=night)) == []


def test_b05_quote_not_in_source():
    own = row(
        "S3",
        "가용 헬기를 집중 투입하십시오.",
        quote="가용 헬기를 집중 투입하십시오.",
        quote_ok=False,
    )
    other = row("S1", "협의하십시오.", quote="초기 대응: 피해면적 10ha 미만", quote_ok=False)
    fine = row("S5", "대피시키십시오.", quote="위험구역 주민은 바로 대피", quote_ok=True)
    found = [f["detail"] for f in checks.find([own, other, fine])]
    assert [d.split(":")[0] for d in found] == ["원문과 다른 인용", "지시문을 인용으로"]


def test_b06_failed_item():
    assert bugs(row("S2", state="failed"), row("S3", "투입하십시오.", state="partial")) == [
        ("B06", "S2")
    ]


def test_b07_shelter_smaller_than_village():
    facts = {
        "shelters": [
            {
                "village": "괴산리",
                "people": 150,
                "village_sites": [{"name": "안평면분회경로당", "capacity": 48}],
                "hub_sites": [{"name": "봉양중학교", "capacity": 605}],
            }
        ]
    }
    small = "괴산리 주민 150명은 안평면분회경로당으로, 넘치면 봉양중학교로 대피하도록 지시하십시오."
    big = "괴산리 주민 150명은 봉양중학교로 대피하도록 지시하십시오."
    assert [f["detail"] for f in checks.find([row("E4", small, facts=facts)])] == [
        "괴산리 150명 → 안평면분회경로당(수용 48명)"
    ]
    assert bugs(row("E4", big, facts=facts)) == []


# ── CSV · 보고 · 비교 ──────────────────────────────────────────────────────


def test_csv_export_loads_like_rows(tmp_path):
    rows = [row("S2", state="failed", proposal_id=3), row("S1", "협의하십시오.", proposal_id=3)]
    path = tmp_path / "proposal_items_202611161612.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "proposal_id", "item_code", "state", "detail", "sections", "evidence"])
        for i, r in enumerate(rows, 1):
            dumped = [
                json.dumps(r[k], ensure_ascii=False) for k in ("detail", "sections", "evidence")
            ]
            w.writerow([i, r["proposal_id"], r["item_code"], r["state"], *dumped])
    loaded = harness.load_rows(path)
    assert [{k: r[k] for k in rows[0]} for r in loaded] == rows
    assert loaded[0]["run"] == 0 and loaded[0]["proposal_id"] == 3


def test_report_and_compare(tmp_path):
    before = [row("S2", state="failed"), row("S1", "협의하십시오.")]
    after = [row("S2", "우선지역을 지정하십시오.", run=1), row("S1", "협의하십시오.", run=1)]
    say = []
    s1 = harness.report(tmp_path / "b", before, label="before", source="csv", say=say.append)
    s2 = harness.report(tmp_path / "a", after, label="after", source="replay", say=say.append)
    assert s1["bugs"]["B06"] | {"examples": []} == {
        "title": checks.BUGS["B06"],
        "outputs": 1,
        "items": 1,
        "findings": 1,
        "by_item": {"S2": 1},
        "examples": [],
    }
    assert s1["outputs"] == 1 and s1["states"] == {"failed": 1, "ok": 1}
    assert "| B06 | 항목 표시 실패(state = failed) | 1/1 (100%) | 1 | 1 |" in say[0]
    assert harness.load_summary(tmp_path / "b") == s1
    assert len(harness.read_jsonl(tmp_path / "b" / harness.FINDINGS)) == 1

    text = harness.compare(s1, s2)
    assert "| B06 | 항목 표시 실패(state = failed) | 1/1 (100%) | 0/1 (0%) | 1 → 0 |" in text
    assert "failed 항목: 1/2 (50%) → 0/2 (0%)" in text
    assert "주의" not in text  # 같은 제안서끼리


def test_cli_check_and_compare(tmp_path, capsys):
    path = tmp_path / "rows.jsonl"
    harness.append_jsonl(path, [row("S2", state="failed", run=1)])
    assert harness.main(["check", str(path), "--label", "x", "--out", str(tmp_path / "x")]) == 0
    assert (
        harness.main(
            ["compare", str(tmp_path / "x"), str(tmp_path / "x"), "--out", str(tmp_path / "c.md")]
        )
        == 0
    )
    assert "1/1 (100%) | 1/1 (100%)" in (tmp_path / "c.md").read_text(encoding="utf-8")
    assert "제안서 검사 — x" in capsys.readouterr().out


def test_cli_replay_dry_run(engine, tmp_path, monkeypatch, capsys):
    """EC2에서 줄 명령 그대로(DB만 가짜): 같은 입력 건너뛰기 · meta · 보고까지."""
    import app.db.session as session

    monkeypatch.setattr(
        session, "SessionLocal", lambda: nullcontext(SimpleNamespace(rollback=dict))
    )
    case = {"incident_id": 1, "input": engine}
    monkeypatch.setattr(harness, "load_inputs", lambda db, ids, rebuild: dict.fromkeys(ids, case))
    out = tmp_path / "dry"
    args = ["replay", "--proposals", "1-2", "--distinct", "--writer", "fake", "--out", str(out)]
    assert harness.main([*args, "--label", "dry"]) == 0
    assert "제안서 2 건너뜀 — 입력이 같은 제안서 1만 돌림" in capsys.readouterr().out
    summary = harness.load_summary(out)
    assert summary["outputs"] == 1 and summary["proposal_ids"] == [1]
    assert all(b["items"] == 0 for b in summary["bugs"].values())
    meta = json.loads((out / harness.META).read_text(encoding="utf-8"))
    assert (meta["writer"], meta["proposals"], meta["model"]) == ("fake", [1, 2], None)
    with pytest.raises(SystemExit):  # 같은 폴더에 다른 설정으로 이어 돌리기는 막는다
        harness.main([*args[:2], "1", *args[3:]])
