# ruff: noqa: E501
"""3단계 LLM 대응 생성: 규칙 엔진 판정 → 근거 검색(RAG) → EXAONE 제안 → 검사 → 조립 (agent/).

    items, logs = run_agent(agent, llm_input, prev, only={"S1", "E2"})
    save(items, logs, round_dir)   # 03_rag/<항목>.json · 04_llm/<항목>.json · 05_output/proposal.json

prev = 직전 회차 결과 {항목: {input_key, proposals, state, item, version}} — 입력이 같은 항목은 다시 쓴다.
"""

import json
from pathlib import Path

from agent.generate import Agent
from agent.situation import flags
from demo.uiseong.data import MapData


def known_names(md: MapData) -> set[str]:
    """지도 자료의 고유 이름(제안 문장에 나온 이름이 판정 · 근거에 있는지 검사할 때)."""
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
    names |= {v.emd for v in md.villages}
    return {n for n in names if len(n) >= 2}


def run_agent(
    agent: Agent, llm_input: dict, prev: dict, only: set[str] | None = None
) -> tuple[list, list]:
    items = [it for it in llm_input["items"] if not only or it["id"] in only]
    common = with_situation(llm_input)
    return agent.run(common, items, prev)


def with_situation(llm_input: dict) -> dict:
    """common + 상황 깃발(agent/situation.py): 항목별 작성 지침(agent/guides.py)을 고르는 데 쓴다.
    깃발은 항목 전체(E2 대피 현황 · S6/E6 요청 건수)로 만든다(--items로 몇 항목만 돌려도 같은 깃발)."""
    f = flags(llm_input["bundles"], llm_input["items"], llm_input["common"])
    return llm_input["common"] | {"situation": f}


def next_prev(items: list[dict], version: str) -> dict:
    return {
        it["id"]: {
            "input_key": it.get("input_key"),
            "proposals": it.get("proposals", []),
            "state": it["state"],
            "item": {k: v for k, v in it.items() if k not in ("input_key", "proposals")},
            "version": version,
        }
        for it in items
        if it["state"] in ("ok", "partial", "reused")
    }


def save(items: list[dict], logs: list[dict], folder: Path) -> None:
    rag, llm, out = folder / "03_rag", folder / "04_llm", folder / "05_output"
    for d in (rag, llm, out):
        d.mkdir(parents=True, exist_ok=True)
    by_id = {it["id"]: it for it in items}
    for lg in logs:
        iid = lg["id"]
        rag_doc = {
            "id": iid,
            "retrieval": lg.get("retrieval"),
            "evidence": lg.get("evidence"),
            "reused_from": lg.get("reused_from"),
        }
        (rag / f"{iid}.json").write_text(
            json.dumps(rag_doc, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        llm_doc = {
            k: lg.get(k)
            for k in (
                "id",
                "input_key",
                "guide",
                "extras",
                "messages",
                "attempts",
                "dropped",
                "evidence_fixed",
                "reused_from",
            )
        }
        llm_doc["state"] = by_id.get(iid, {}).get("state")
        (llm / f"{iid}.json").write_text(
            json.dumps(llm_doc, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        msgs = lg.get("messages") or []
        if msgs:  # 사람이 바로 읽을 수 있게 프롬프트 전문을 글로도
            txt = "\n\n".join(f"===== {m['role']} =====\n{m['content']}" for m in msgs)
            for i, at in enumerate(lg.get("attempts", []), 1):
                txt += f"\n\n===== 답 {i}차 =====\n{at.get('raw', '')}\n\n검사: {json.dumps(at.get('errors'), ensure_ascii=False)}"
            (llm / f"{iid}_prompt.txt").write_text(txt, encoding="utf-8")
    proposal = [{k: v for k, v in it.items() if k != "input_key"} for it in items]
    (out / "proposal.json").write_text(
        json.dumps(proposal, ensure_ascii=False, indent=1), encoding="utf-8"
    )
