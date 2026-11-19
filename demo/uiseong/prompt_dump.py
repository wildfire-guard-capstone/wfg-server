# ruff: noqa: E501
"""평가 조건별 프롬프트를 LLM 없이 만들어 저장한다(토큰 실측 · 시범 실행용, 11/19 재설계 단계 2).

    python -m demo.uiseong.prompt_dump --input <demo-uiseong/input> --deck <ELMFIRE 덱> --spread <runs/e45-1109>
        --out <prompts.json> [--conditions C1,C2,C3] [--rounds 1,2,3,4] [--centers-only]
        [--evidence http://127.0.0.1:18042 | (없으면 가짜 근거)] [--trial <폴더>]

- 회차마다 규칙 판정(step2_rules.run_rules, 지금 코드)을 다시 돌리고, 조건마다 Agent.prepare로 첫 시도 메시지를 만든다
  (검색은 근거 서버를 부른다 — 생성은 하지 않음). 출력 = [{"id": "C1-r1-S1", "condition", "round", "item", "messages"}].
- --trial: 가짜 LLM(검사를 통과하는 고정 답)으로 생성 흐름 전체를 돌려 run_demo와 같은 꼴(round_N/03_rag · 04_llm · 05_output)로
  저장한다(저장 꼴 확인용).
"""

import argparse
import json
from pathlib import Path

from agent.generate import Agent
from demo.uiseong import raw_table, step1_spread, step2_rules, step3_agent
from demo.uiseong import rounds as rounds_mod
from demo.uiseong.data import MapData


class FakeEvidence:
    """근거 서버 없이: 항목 질의마다 같은 가짜 청크 5개."""

    def fetch(self, ids):
        return [], list(ids)

    def search(self, query, k=5, exclude=()):
        return [
            {
                "chunk_id": f"FAKE-{i}",
                "doc_id": "FAKE",
                "doc_name": "가짜 근거",
                "section": None,
                "page": None,
                "text": f"가짜 근거 원문 {i}: 시험용 문장입니다. " * 20,
                "score": 1.0 - i / 10,
            }
            for i in range(1, k + 1)
        ]


class FakeLLM:
    """검사를 통과하게 짠 고정 답(시범 실행 저장 꼴 확인용)."""

    model = "fake"

    def __init__(self):
        self.calls = []

    def chat(self, messages, schema=None, **kw):
        self.calls.append({"tag": kw.get("tag")})
        items = schema["properties"]["proposals"]["items"]["properties"]
        p = {"category": "대기", "text": "현장 상황을 확인하십시오."}
        if "evidence" in items:
            ev = items["evidence"]["items"]["enum"]
            p |= {"evidence": ev[:1], "quote": "시험용 문장입니다"}
        return json.dumps(
            {
                "order": "상황 → 상태 → 판단 → 제안",
                "situation": "불이 번지고 있습니다.",
                "status": "",
                "judgment": "",
                "proposals": [p],
            },
            ensure_ascii=False,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--deck", type=Path, required=True)
    ap.add_argument(
        "--spread", type=Path, required=True, help="round_N/01_spread가 든 앞 결과 폴더"
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--conditions", default="C1,C2,C3")
    ap.add_argument("--rounds", default="1,2,3,4")
    ap.add_argument("--centers-only", action="store_true")
    ap.add_argument("--near", default="ignition", choices=["ignition", "range"])
    ap.add_argument("--coords", default="wgs84", choices=["wgs84", "local"])
    ap.add_argument("--evidence", default="")
    ap.add_argument("--trial", type=Path)
    a = ap.parse_args()
    from agent.clients import Evidence

    ev = Evidence(a.evidence) if a.evidence else FakeEvidence()
    md, sc = MapData(a.input), rounds_mod.load(a.input)
    conds = [c.strip() for c in a.conditions.split(",") if c.strip()]
    wanted = {int(x) for x in a.rounds.split(",") if x.strip()}
    rows, prev_stage, prev_alerts = [], None, ()
    prevs = {c: {} for c in conds}
    for rd in sc.rounds:
        sp = step1_spread.load(a.spread / f"round_{rd.no}" / "01_spread")
        stage_before, alerts_before = prev_stage, prev_alerts
        ru = step2_rules.run_rules(md, rd, sc, sp, a.deck, prev_stage, prev_alerts)
        prev_stage, prev_alerts = ru["next_prev_stage"], ru["next_prev_alerts"]
        if rd.no not in wanted:
            continue
        llm_in = ru["llm_input"]
        raw = raw_table.build(
            md, rd, sp, ru["inputs"], stage_before, alerts_before, a.centers_only, a.near, a.coords
        )
        for c in conds:
            common = step3_agent.with_situation(llm_in)
            if c != "C3":
                common = common | {"raw_table": raw}
            agent = Agent(FakeLLM(), ev, step3_agent.known_names(md), workers=1, condition=c)
            for it in llm_in["items"]:
                msgs, _, _ = agent.prepare(common, it, None)
                rows.append(
                    {
                        "id": f"{c}-r{rd.no}-{it['id']}",
                        "condition": c,
                        "round": rd.no,
                        "item": it["id"],
                        "messages": msgs,
                    }
                )
            if a.trial:
                rdir = a.trial / f"evalv3trial-{c}" / f"round_{rd.no}"
                (rdir / "02_rules").mkdir(parents=True, exist_ok=True)
                (rdir / "02_rules" / "llm_input.json").write_text(
                    json.dumps(llm_in, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
                )
                if c != "C3":
                    (rdir / "02_rules" / "raw_table.txt").write_text(raw, encoding="utf-8")
                items, logs = step3_agent.run_agent(
                    agent, llm_in, prevs[c], None, raw if c != "C3" else ""
                )
                step3_agent.save(items, logs, rdir)
                prevs[c] |= step3_agent.next_prev(items, rd.version)
                print(c, rd.no, {i["id"]: i["state"] for i in items}, flush=True)
        print(f"회차 {rd.no}: 원자료 표 {len(raw):,}자", flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"프롬프트 {len(rows)}개 → {a.out}", flush=True)


if __name__ == "__main__":
    main()
