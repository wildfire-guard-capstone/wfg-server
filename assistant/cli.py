"""데모 · 시험용 명령: 의성 데모 회차 결과(제안서)를 보고 어시스턴트에 묻는다(DB 없이).

    python -m assistant.cli --round runs/tune15/round_1 "괴산리 대피 언제까지 해야 해?"
    python -m assistant.cli --round runs/tune15/round_1 --llm http://127.0.0.1:8031 \\
        --evidence http://127.0.0.1:8032 --batch assistant/eval/questions.json --out out.json

--llm을 빼면 규칙만(LLM 없음) 쓴다. 결과는 입력마다 {input, item_code, result} 한 줄 요약을 찍고,
--out이면 trace까지 JSON으로 남긴다.
"""

import argparse
import json
import sys
from pathlib import Path

from agent.clients import LLM, Evidence
from assistant.context import from_round
from assistant.correction import CorrectionError
from assistant.service import Assistant, AssistantUnavailable


def show(r: dict) -> str:
    t = r.get("type")
    if t == "ANSWER":
        cites = "; ".join(f"[{c['k']}] {c.get('label')}" for c in r.get("citations", []))
        tools = ", ".join(f"{x['tool']}({x['arg']})" if x["arg"] else x["tool"] for x in r["tools"])
        return f"ANSWER/{r['route']} tools={tools}\n  {r['answer']}\n  근거: {cites or '-'}"
    if t == "CORRECTION_PREVIEW":
        lines = "\n".join(f"  - {c['label']}" for c in r["changes"])
        ign = "\n".join(f"  (무시) {i['text']}: {i['reason']}" for i in r.get("ignored", []))
        return f"CORRECTION_PREVIEW\n{lines}" + (f"\n{ign}" if ign else "")
    if t == "UNSUPPORTED":
        return f"UNSUPPORTED  {r['reason']}"
    return f"ERROR {r.get('status')} {r.get('code')}  {r.get('message')}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--round", required=True, help="데모 회차 폴더(02_rules · 05_output이 있는 곳)")
    ap.add_argument("--llm", help="vLLM 주소(없으면 규칙만)")
    ap.add_argument("--model", default="exaone-4.5-33b-awq")
    ap.add_argument("--evidence", help="근거 서버 주소(없으면 제안서가 인용한 원문만)")
    ap.add_argument("--villages", help="정정 대상 마을 목록 JSON [{id, name, emd}]")
    ap.add_argument("--item", help="「이 항목 질문」 항목 코드")
    ap.add_argument("--batch", help="입력 목록 JSON [{text, item_code?}]")
    ap.add_argument("--out", help="결과 JSON(trace 포함)")
    ap.add_argument("text", nargs="*")
    a = ap.parse_args(argv)

    villages = json.loads(Path(a.villages).read_text(encoding="utf-8")) if a.villages else None
    ctx = from_round(a.round, villages)
    inputs = [{"text": t, "item_code": a.item} for t in a.text]
    if a.batch:
        inputs += json.loads(Path(a.batch).read_text(encoding="utf-8"))
    results = []
    for q in inputs:
        llm = LLM(url=a.llm, model=a.model, timeout=300) if a.llm else None
        ev = Evidence(url=a.evidence, timeout=60) if a.evidence else None
        try:
            r = Assistant(ctx, llm, ev).handle(q["text"], q.get("item_code"))
        except CorrectionError as e:
            r = {"type": "ERROR", "status": e.status, "code": e.code, "message": e.message}
        except AssistantUnavailable as e:
            r = {"type": "ERROR", "status": 503, "code": "AI_UNAVAILABLE", "message": str(e)}
        head = f"▶ {q['text']}" + (f"  (항목 {q['item_code']})" if q.get("item_code") else "")
        secs = (r.get("trace") or {}).get("seconds")
        print(f"{head}  [{secs}s]\n{show(r)}\n", flush=True)
        results.append({**q, "result": r})
    if a.out:
        Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
