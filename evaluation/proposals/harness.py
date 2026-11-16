"""제안서 하네스 명령줄: replay(EC2) · check · compare. 사용법은 __init__.py.

replay는 DB에 저장된 제안서의 엔진 입력(proposals.context · proposal_items.detail)을 그대로 지금
작성기(설정 PROPOSAL_WRITER — EC2에서는 GPU 서버 EXAONE)에 다시 넣는다. API의 제안 작성과 같은
`writer.write()`를 부르고, DB에는 아무것도 쓰지 않는다. 같은 입력을 고치기 전 · 후 코드로 돌려
check 건수를 비교한다(LLM 출력은 매번 달라 --repeat로 여러 번).

결과 폴더(기본 results/proposals/<label>, 커밋하지 않음 — 근거 원문이 들어 있다):
    rows.jsonl      항목마다 1줄(proposal_items 모양 + run)
    logs.jsonl      항목마다 검사 오류 · 뺀 제안 · 근거 목록(보낸 프롬프트 · 받은 답 원문은 뺌)
    meta.json       무엇으로 돌렸는지(제안서 · 작성기 · 모델 · 항목 다시 만들기)
    summary.json    건수(check) · findings.jsonl 사례 · report.md 표
"""

import argparse
import csv
import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from evaluation.proposals import checks

ORDER = [f"{axis}{n}" for axis in "SE" for n in range(1, 7)]  # rules/llm_input.py ITEMS 순서
JSON_COLUMNS = ("detail", "sections", "evidence")
IN_PROGRESS = "진행 중"  # 제안서는 진행 중인 산불에만 저장된다 — 지금 종료여도 그때 모습으로
RESULTS = Path("results/proposals")
ROWS, LOGS, META = "rows.jsonl", "logs.jsonl", "meta.json"
SUMMARY, FINDINGS, REPORT = "summary.json", "findings.jsonl", "report.md"
SAME_SETUP = ("proposals", "writer", "rebuild_items")  # 이어서 돌릴 때 같아야 하는 설정

Row = dict[str, Any]
Say = Callable[[str], None]


# ── 입력 ───────────────────────────────────────────────────────────────────


def parse_ids(spec: str) -> list[int]:
    """「1-3,7」 → [1, 2, 3, 7](적은 순서대로, 중복 없이)."""
    ids: list[int] = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        lo, _, hi = part.partition("-")
        start, end = int(lo), int(hi or lo)
        if start > end:
            raise ValueError(f"범위가 거꾸로입니다: {part}")
        ids += [i for i in range(start, end + 1) if i not in ids]
    if not ids:
        raise ValueError("제안서 id가 없습니다")
    return ids


def replay_input(
    bundles: dict[str, Any], items: list[dict[str, Any]], incident: dict[str, Any]
) -> dict[str, Any]:
    """저장된 묶음 · 항목 → 작성기 입력(rules.llm_input.build_llm_input과 같은 모양)."""
    from rules.llm_input import build_common

    return {"bundles": bundles, "common": build_common(bundles, incident), "items": items}


def load_inputs(db: Any, ids: list[int], rebuild_items: bool = False) -> dict[int, dict[str, Any]]:
    """제안서마다 {"incident_id", "input"}. 항목은 저장된 detail 그대로(기본), rebuild_items면
    저장된 묶음으로 지금 코드의 rules/items · rules/sections를 다시 돌린다."""
    from app.db.models import Incident, Proposal
    from app.repositories.engine_inputs import incident_info
    from app.repositories.proposals import items_of
    from rules.llm_input import build_items

    out = {}
    for pid in ids:
        p = db.get(Proposal, pid)
        if p is None:
            raise SystemExit(f"DB에 제안서 {pid} 없음")
        incident = incident_info(db, db.get(Incident, p.incident_id)) | {"status": IN_PROGRESS}
        if rebuild_items:
            items = build_items(p.context)
        else:
            stored = {it.item_code: it.detail for it in items_of(db, pid)}
            items = [stored[code] for code in ORDER if code in stored]
        out[pid] = {"incident_id": p.incident_id, "input": replay_input(p.context, items, incident)}
    return out


def fingerprint(llm_input: dict[str, Any]) -> str:
    raw = json.dumps(llm_input, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def distinct(inputs: dict[int, dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], dict[int, int]]:
    """입력이 같은 제안서는 처음 것만 남긴다. 돌려주는 것: (남긴 것, {뺀 id: 같은 입력의 id})."""
    first: dict[str, int] = {}
    kept, same = {}, {}
    for pid, case in inputs.items():
        key = fingerprint(case["input"])
        if key in first:
            same[pid] = first[key]
        else:
            first[key] = pid
            kept[pid] = case
    return kept, same


# ── 다시 돌리기 ────────────────────────────────────────────────────────────


@contextmanager
def agent_logs() -> Iterator[list[dict[str, Any]]]:
    """에이전트 기록(agent.generate.Agent.run이 돌려주는 logs)을 옆에서 모은다.
    작성기는 API와 똑같이 writer.write()로 부르고, 기록만 가로챈다(가짜 작성기면 빈 목록)."""
    from agent.generate import Agent

    captured: list[dict[str, Any]] = []
    original = Agent.run

    def recording(self: Any, *args: Any, **kwargs: Any) -> Any:
        items, logs = original(self, *args, **kwargs)
        captured.extend(logs)
        return items, logs

    Agent.run = recording
    try:
        yield captured
    finally:
        Agent.run = original


def compact_log(log: dict[str, Any]) -> dict[str, Any]:
    """이유만 남긴다: 시도별 검사 오류 · 끝까지 걸려 뺀 제안 · 근거 목록 · 상황 문장."""
    return {
        "item_code": log.get("id"),
        "input_key": log.get("input_key"),
        "errors": [a.get("errors") for a in log.get("attempts") or []],
        "dropped": log.get("dropped") or [],
        "evidence": log.get("evidence") or [],
        "situations": log.get("situations") or {},
    }


def rows_of(
    run: int, proposal_id: int, incident_id: int, llm_input: dict[str, Any], written: list[Row]
) -> list[Row]:
    """작성기 결과 → proposal_items 모양 행(app.repositories.proposals.save_proposal과 같은 칸)."""
    details = {it["id"]: it for it in llm_input["items"]}
    return [
        {
            "run": run,
            "proposal_id": proposal_id,
            "incident_id": incident_id,
            "item_code": w["id"],
            "state": w["state"],
            "text": "\n".join(p["body"] for p in checks.paragraphs(w)),  # = text_of(sections)
            "sections": w["sections"],
            "evidence": w["evidence"],
            "detail": details.get(w["id"]),
            "input_key": w.get("input_key"),
        }
        for w in written
    ]


def replay(
    inputs: dict[int, dict[str, Any]],
    writer: Any,
    out: Path,
    *,
    repeat: int = 1,
    say: Say = print,
) -> list[Row]:
    """제안서마다 repeat번 작성기를 돌려 rows.jsonl · logs.jsonl에 덧붙인다(run 1을 다 돌고 run 2).
    끊겨도 같은 명령을 다시 주면 끝난 (run, 제안서)는 건너뛴다."""
    out.mkdir(parents=True, exist_ok=True)
    done = {(r["run"], r["proposal_id"]) for r in read_jsonl(out / ROWS)}
    todo = [(run, pid) for run in range(1, repeat + 1) for pid in inputs if (run, pid) not in done]
    if done:
        say(f"이미 끝난 출력 {len(done)}개는 건너뜁니다")
    for n, (run, pid) in enumerate(todo, 1):
        case = inputs[pid]
        started = time.monotonic()
        with agent_logs() as logs:
            written = writer.write(case["input"])
        rows = rows_of(run, pid, case["incident_id"], case["input"], written)
        append_jsonl(out / ROWS, rows)
        append_jsonl(out / LOGS, [{"run": run, "proposal_id": pid} | compact_log(g) for g in logs])
        states = " · ".join(f"{k} {v}" for k, v in Counter(r["state"] for r in rows).items())
        took = time.monotonic() - started
        say(f"[{n}/{len(todo)}] run {run} · 제안서 {pid}: {states} ({took:.0f}초)")
    return read_jsonl(out / ROWS)


# ── 파일 ───────────────────────────────────────────────────────────────────


def read_jsonl(path: Path) -> list[Row]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append_jsonl(path: Path, rows: Iterable[Row]) -> None:
    text = "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)  # 출력 하나를 한 번에 — 끊겨도 반쪽 출력이 남지 않게


def read_csv(path: Path) -> list[Row]:
    """DB에서 내보낸 proposal_items CSV(JSON 칸은 글) → 행. 저장된 출력이라 run = 0."""
    csv.field_size_limit(2**31 - 1)  # 근거 원문 칸이 기본 한도(128 KB)를 넘을 수 있다
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in JSON_COLUMNS:
            r[k] = json.loads(r[k]) if r.get(k) else None
        r["proposal_id"] = int(r["proposal_id"])
        r["run"] = 0
    return rows


def load_rows(path: Path) -> list[Row]:
    """CSV(DB 내보내기) · rows.jsonl · 결과 폴더(안의 rows.jsonl)."""
    if path.is_dir():
        path = path / ROWS
    if not path.exists():
        raise SystemExit(f"없는 파일: {path}")
    return read_csv(path) if path.suffix.lower() == ".csv" else read_jsonl(path)


def load_summary(path: Path) -> dict[str, Any]:
    path = path / SUMMARY if path.is_dir() else path
    if not path.exists():
        raise SystemExit(f"없는 파일: {path} — check나 replay를 먼저")
    return json.loads(path.read_text(encoding="utf-8"))


# ── 보고 ───────────────────────────────────────────────────────────────────


def _share(n: int, total: int) -> str:
    return f"{n}/{total} ({n / total:.0%})" if total else "-"


def markdown(s: dict[str, Any]) -> str:
    states = " · ".join(f"{k} {v}" for k, v in sorted(s["states"].items()))
    rate = s["quote_match_rate"]
    lines = [
        f"# 제안서 검사 — {s['label']}",
        "",
        f"- 출처: {s['source']}",
        f"- 출력 {s['outputs']}개(제안서 {len(s['proposal_ids'])}개 × run {s['runs']}) · "
        f"항목 {s['items']}개 · 문단 {s['paragraphs']}개",
        f"- 항목 상태: {states} · 인용 일치 {'-' if rate is None else f'{rate:.1%}'}",
        "",
        "| 버그 | 내용 | 걸린 출력 | 걸린 항목 | 사례 |",
        "|---|---|---:|---:|---:|",
    ]
    for bug, b in s["bugs"].items():
        lines.append(
            f"| {bug} | {b['title']} | {_share(b['outputs'], s['outputs'])} "
            f"| {b['items']} | {b['findings']} |"
        )
    lines += ["", "## 예시", ""]
    lines += [f"- {bug} {e}" for bug, b in s["bugs"].items() for e in b["examples"]] or ["- 없음"]
    return "\n".join(lines) + "\n"


def report(out: Path, rows: list[Row], *, label: str, source: str, say: Say = print) -> dict:
    """검사해 summary.json · findings.jsonl · report.md를 쓰고 표를 보여 준다."""
    out.mkdir(parents=True, exist_ok=True)
    findings = checks.find(rows)
    summary = {"label": label, "source": source} | checks.summarize(rows, findings)
    (out / SUMMARY).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / FINDINGS).write_text("", encoding="utf-8")
    append_jsonl(out / FINDINGS, findings)
    text = markdown(summary)
    (out / REPORT).write_text(text, encoding="utf-8")
    say(text)
    return summary


def compare(before: dict[str, Any], after: dict[str, Any]) -> str:
    """두 요약을 나란히: 버그마다 걸린 출력 비율(출력 수가 달라도 비교되게)."""
    nb, na = before["outputs"], after["outputs"]
    lines = [f"# 제안서 검사 비교 — {before['label']} → {after['label']}", ""]
    if before["proposal_ids"] != after["proposal_ids"]:
        lines += ["> 주의: 두 쪽의 제안서가 다릅니다 — 같은 입력끼리 비교해야 공정합니다.", ""]
    lines += [
        f"출력 {nb}개 → {na}개",
        "",
        "| 버그 | 내용 | 전: 걸린 출력 | 후: 걸린 출력 | 전 → 후: 걸린 항목 |",
        "|---|---|---:|---:|---:|",
    ]
    for bug, b in before["bugs"].items():
        a = after["bugs"].get(bug, {"outputs": 0, "items": 0})
        lines.append(
            f"| {bug} | {b['title']} | {_share(b['outputs'], nb)} | {_share(a['outputs'], na)} "
            f"| {b['items']} → {a['items']} |"
        )
    failed = (before["states"].get("failed", 0), after["states"].get("failed", 0))
    rate = (before["quote_match_rate"], after["quote_match_rate"])
    lines += [
        "",
        f"- failed 항목: {_share(failed[0], before['items'])} → "
        f"{_share(failed[1], after['items'])}",
        f"- 인용 일치: {rate[0]} → {rate[1]}",
    ]
    return "\n".join(lines) + "\n"


# ── 명령 ───────────────────────────────────────────────────────────────────


def make_writer(name: str) -> Any:
    if name == "fake":
        from app.propose.fake import FakeWriter

        return FakeWriter()
    from app.services.prediction import writer_from_settings

    return writer_from_settings()


def check_meta(out: Path, meta: dict[str, Any]) -> None:
    """이어서 돌릴 때 설정이 같은지 본다. 처음이면 meta.json을 쓴다."""
    path = out / META
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        diff = [k for k in SAME_SETUP if old.get(k) != meta.get(k)]
        if diff:
            raise SystemExit(
                f"{out}에 다른 설정({', '.join(diff)})의 결과가 있습니다 — --out을 바꾸세요"
            )
        return
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_replay(args: argparse.Namespace) -> None:
    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.propose.agent import WriterUnavailable

    out = args.out or RESULTS / args.label
    settings = get_settings()
    writer = make_writer(args.writer)
    meta = {
        "label": args.label,
        "proposals": args.proposals,
        "writer": getattr(writer, "name", args.writer),
        "model": settings.llm_model if args.writer == "settings" else None,
        "rebuild_items": args.rebuild_items,
        "started_at": f"{datetime.now():%Y-%m-%d %H:%M}",
    }
    check_meta(out, meta)
    with SessionLocal() as db:
        inputs = load_inputs(db, args.proposals, args.rebuild_items)
        db.rollback()
    if args.distinct:
        inputs, same = distinct(inputs)
        for pid, first in same.items():
            print(f"제안서 {pid} 건너뜀 — 입력이 같은 제안서 {first}만 돌림")
    print(f"작성기 {meta['writer']} · 제안서 {list(inputs)} × {args.repeat}번 → {out}")
    try:
        rows = replay(inputs, writer, out, repeat=args.repeat)
    except WriterUnavailable as e:
        raise SystemExit(
            f"{e}. 끝난 출력은 {out}에 남았습니다 — 같은 명령으로 이어서 돌리세요."
        ) from e
    report(out, rows, label=args.label, source=f"replay {meta['writer']} ({meta['model']})")


def cmd_check(args: argparse.Namespace) -> None:
    label = args.label or args.path.stem
    report(args.out or RESULTS / label, load_rows(args.path), label=label, source=str(args.path))


def cmd_compare(args: argparse.Namespace) -> None:
    text = compare(load_summary(args.before), load_summary(args.after))
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    from evaluation import proposals as package

    parser = argparse.ArgumentParser(
        prog="python -m evaluation.proposals",
        description=package.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("replay", help="저장된 입력으로 작성기를 다시 돌린다(DB · 작성기 필요)")
    p.add_argument("--proposals", type=parse_ids, required=True, help="제안서 id(예: 1-11 · 1,3)")
    p.add_argument("--repeat", type=int, default=1, help="제안서마다 몇 번(기본 1)")
    p.add_argument("--label", default="replay", help="결과 이름(예: before · after)")
    p.add_argument("--out", type=Path, help=f"결과 폴더(기본 {RESULTS}/<label>)")
    p.add_argument("--writer", choices=["settings", "fake"], default="settings")
    p.add_argument("--distinct", action="store_true", help="입력이 같은 제안서는 한 번만")
    p.add_argument(
        "--rebuild-items",
        action="store_true",
        help="항목 입력을 저장된 것 대신 지금 코드(rules/items · sections)로 다시 만든다",
    )
    p.set_defaults(run=cmd_replay)

    p = sub.add_parser("check", help="CSV(proposal_items 내보내기) · rows.jsonl을 검사만")
    p.add_argument("path", type=Path)
    p.add_argument("--label", help="결과 이름(기본: 파일 이름)")
    p.add_argument("--out", type=Path, help=f"결과 폴더(기본 {RESULTS}/<label>)")
    p.set_defaults(run=cmd_check)

    p = sub.add_parser("compare", help="두 결과(summary.json)를 나란히")
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--out", type=Path, help="비교 표를 쓸 파일(.md)")
    p.set_defaults(run=cmd_compare)

    args = parser.parse_args(argv)
    if getattr(args, "repeat", 1) < 1:
        parser.error("--repeat는 1 이상")
    args.run(args)
    return 0
