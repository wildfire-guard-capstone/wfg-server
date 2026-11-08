# ruff: noqa: E501
"""생성 결과 검증: 기존 시나리오 · 백서 기록과 비교하고 근거 뒷받침을 본다(GPU 서버, EXAONE 판정).

    python -m demo.uiseong.verify --runs <결과 폴더> --ref <demo-uiseong/reference> [--llm http://127.0.0.1:8031] [--no-llm]
    → <결과 폴더>/verify.json · <결과 폴더>/검증_보고.md

1. 자동(LLM 없이): 항목 상태 · 제안 수 · 뺀 제안 · 근거 문서 분포, 회차 1 목표 문단의 근거 쪽을 검색이 찾았는지(검색 재현율)
2. 근거 뒷받침(EXAONE): 제안마다 단 근거 원문에 그 조치가 적혀 있는지(예 · 일부 · 아니오)
3. 회차 1 목표 대비(EXAONE): 시나리오 v3 회차 1 목표 문단(reference/scenario_v3_r1_gold.json, 사용자가 11/7 목표로 확정)의
   조치를 같은 항목의 생성 제안이 다루는지(예 · 일부 · 아니오). 목표 문단은 대리 면적으로 만들어 숫자는 비교하지 않는다
4. 백서 실제 기록 대비(EXAONE): 회차 시각 뒤에 실제로 한 결정 · 조치(reference/actual_records.json)를
   그 회차 제안이 미리 다뤘는지. 당시 대응을 평가하는 것이 아니라 제안이 현실의 조치와 같은 방향인지 보는 것이다

판정 모델이 생성 모델과 같은 EXAONE이라 후하게 볼 수 있다(보고서에 적는다).
"""

import argparse
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.clients import LLM

JUDGE_SYSTEM = "당신은 산불 대응 제안서를 검토하는 평가자입니다. 주어진 기준대로만 판정하고 JSON 하나만 출력합니다."
VERDICT = {"type": "string", "enum": ["예", "일부", "아니오"]}


def _schema(n: int, key: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "minItems": n,
                "maxItems": n,
                "items": {
                    "type": "object",
                    "properties": {
                        "no": {"type": "integer"},
                        key: VERDICT,
                        "reason": {"type": "string"},
                    },
                    "required": ["no", key, "reason"],
                },
            }
        },
        "required": ["results"],
    }


def _ask(llm: LLM, user: str, n: int, key: str, tag: str) -> list[dict]:
    raw = llm.chat(
        [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}],
        schema=_schema(n, key),
        temperature=0.0,
        max_tokens=1500,
        tag=tag,
    )
    raw = raw.split("</think>")[-1]
    return json.loads(raw[raw.find("{") : raw.rfind("}") + 1])["results"]


def paragraphs(item: dict) -> list[dict]:
    return [
        b
        for s in item.get("sections", [])
        for b in s.get("blocks", [])
        if b.get("type") == "paragraph"
    ]


def judge_grounded(llm: LLM, rd: dict, item: dict) -> list[dict]:
    paras = paragraphs(item)
    if not paras:
        return []
    ev = {e["chunk_id"]: e for e in item.get("evidence", [])}
    lines = []
    for i, p in enumerate(paras, 1):
        cited = "\n".join(
            f"  ({ev[c]['label']}) {ev[c]['text'][:900]}" for c in p.get("evidence", []) if c in ev
        )
        lines.append(f"[제안 {i}] {p['text']}\n[제안 {i}의 근거 원문]\n{cited}")
    user = (
        "각 제안이 권하는 조치(할 일)가 그 제안의 근거 원문에 적혀 있는지 판정하십시오. 숫자 · 마을 이름 · 시각은 상황 값이라 보지 않습니다.\n"
        "예 = 조치가 원문에 있음, 일부 = 조치 일부만 있거나 원문보다 넓게 씀, 아니오 = 원문에 없는 조치.\n\n"
        + "\n\n".join(lines)
        + '\n\n출력: {"results": [{"no": 제안 번호, "supported": "예|일부|아니오", "reason": "한 문장"}]}'
    )
    res = _ask(llm, user, len(paras), "supported", f"ground-{rd['round']['no']}-{item['id']}")
    return [{"text": p["text"], **r} for p, r in zip(paras, res, strict=False)]


def judge_gold(llm: LLM, item: dict, gold_paras: list[dict]) -> list[dict]:
    gen = "\n".join(f"- {p['text']}" for p in paragraphs(item)) or "(제안 없음)"
    gl = "\n".join(f"[목표 {i}] {g['text']}" for i, g in enumerate(gold_paras, 1))
    user = (
        "아래 [생성 제안]이 각 [목표] 문단의 조치(누구에게 무엇을 하라는지)를 다루는지 판정하십시오. 숫자 · 시각 · 면적은 계산 방식이 달라 비교하지 않습니다.\n"
        "예 = 같은 조치가 있음, 일부 = 비슷하지만 대상이나 할 일이 일부만 같음, 아니오 = 없음.\n\n"
        f"[생성 제안]\n{gen}\n\n{gl}\n\n"
        '출력: {"results": [{"no": 목표 번호, "covered": "예|일부|아니오", "reason": "한 문장"}]}'
    )
    res = _ask(llm, user, len(gold_paras), "covered", f"gold-{item['id']}")
    return [{"gold": g["text"], **r} for g, r in zip(gold_paras, res, strict=False)]


def judge_actual(llm: LLM, rd: dict, item: dict, recs: list[dict]) -> list[dict]:
    gen = "\n".join(f"- {p['text']}" for p in paragraphs(item)) or "(제안 없음)"
    rl = "\n".join(
        f"[기록 {i}] {r['when']} {r['action']} (주체: {r['by']})" for i, r in enumerate(recs, 1)
    )
    user = (
        f"{rd['round']['at']} 제안서의 [생성 제안]이, 그 뒤에 실제로 한 각 [기록]의 결정 · 조치를 미리 권했는지 판정하십시오.\n"
        "예 = 같은 조치를 권함, 일부 = 같은 방향이지만 대상 · 내용이 일부만 같음, 아니오 = 권하지 않음.\n\n"
        f"[생성 제안]\n{gen}\n\n{rl}\n\n"
        '출력: {"results": [{"no": 기록 번호, "anticipated": "예|일부|아니오", "reason": "한 문장"}]}'
    )
    res = _ask(llm, user, len(recs), "anticipated", f"actual-{rd['round']['no']}-{item['id']}")
    return [{"record": r["id"], **x} for r, x in zip(recs, res, strict=False)]


def _mins(hm: str) -> int:
    h, m = map(int, hm.split(":"))
    return h * 60 + m


def automatic(rounds: list[dict], gold: dict) -> dict:
    out = {"rounds": []}
    for rd in rounds:
        items = rd["proposal"] or []
        docs = Counter()
        for it in items:
            ev = {e["chunk_id"]: e for e in it.get("evidence", [])}
            for p in paragraphs(it):
                for c in p.get("evidence", []):
                    docs[
                        "표준매뉴얼"
                        if c.startswith("SM-")
                        else (ev.get(c, {}).get("doc_name") or c.split("-")[0])
                    ] += 1
        out["rounds"].append(
            {
                "at": rd["round"]["at"],
                "states": dict(Counter(it["state"] for it in items)),
                "proposals": sum(len(paragraphs(it)) for it in items),
                "dropped": sum(len(lg.get("dropped") or []) for lg in rd.get("agent_log") or []),
                "llm_calls": len(rd.get("llm_calls") or []),
                "evidence_docs": dict(docs.most_common()),
            }
        )
    # 회차 1 목표 문단의 근거 쪽을 검색이 찾았는지 · 생성 제안이 썼는지
    r1 = rounds[0]
    logs = {lg["id"]: lg for lg in r1.get("agent_log") or []}
    props = {it["id"]: it for it in r1["proposal"] or []}
    rows = []
    for g in gold["items"]:
        want = set()
        for p in g.get("paragraphs", []):
            for m in re.finditer(r"\[([^\[\]]+)\]", p["text"]):
                for part in m.group(1).split(","):
                    part = part.strip()
                    mm = re.fullmatch(r"p\.(\d+(?:·\d+)*)", part)
                    if mm:
                        want |= {f"p.{n}" for n in mm.group(1).split("·")}
        found = {e[1] for e in (logs.get(g["id"], {}).get("evidence") or [])}
        used = {e["label"] for e in props.get(g["id"], {}).get("evidence", [])}
        if want:
            rows.append(
                {
                    "item": g["id"],
                    "gold_pages": sorted(want),
                    "retrieved": sorted(want & found),
                    "cited": sorted(want & used),
                }
            )
    tot = sum(len(r["gold_pages"]) for r in rows)
    out["gold_page_recall"] = {
        "rows": rows,
        "retrieved": round(sum(len(r["retrieved"]) for r in rows) / tot, 3) if tot else None,
        "cited": round(sum(len(r["cited"]) for r in rows) / tot, 3) if tot else None,
    }
    return out


def score(vals: list[str]) -> dict:
    c = Counter(vals)
    n = sum(c.values()) or 1
    return {
        "n": sum(c.values()),
        "예": c["예"],
        "일부": c["일부"],
        "아니오": c["아니오"],
        "점수": round((c["예"] + 0.5 * c["일부"]) / n, 3),
    }


def report(v: dict) -> str:
    L = ["# 의성 데모 검증 보고", "", f"결과 폴더: `{v['runs']}`", ""]
    L += [
        "## 1. 생성 현황",
        "",
        "| 회차 | 항목 상태 | 제안 | 뺀 제안 | LLM 호출 | 근거 문서(인용 수) |",
        "|---|---|---|---|---|---|",
    ]
    for r in v["auto"]["rounds"]:
        L.append(
            f"| {r['at']} | {r['states']} | {r['proposals']} | {r['dropped']} | {r['llm_calls']} | {r['evidence_docs']} |"
        )
    g = v["auto"]["gold_page_recall"]
    L += [
        "",
        f"회차 1 목표 문단의 매뉴얼 근거 쪽: 검색이 찾은 비율 **{g['retrieved']}**, 생성 제안이 인용한 비율 **{g['cited']}**",
        "",
    ]
    L += ["| 항목 | 목표 근거 쪽 | 검색으로 찾음 | 제안이 인용 |", "|---|---|---|---|"]
    for r in g["rows"]:
        L.append(
            f"| {r['item']} | {', '.join(r['gold_pages'])} | {', '.join(r['retrieved']) or '—'} | {', '.join(r['cited']) or '—'} |"
        )
    if "grounded" in v:
        L += [
            "",
            "## 2. 근거 뒷받침(제안의 조치가 단 근거 원문에 있는가)",
            "",
            "| 회차 | 제안 | 예 | 일부 | 아니오 | 점수 |",
            "|---|---|---|---|---|---|",
        ]
        for at, s in v["grounded_score"].items():
            L.append(f"| {at} | {s['n']} | {s['예']} | {s['일부']} | {s['아니오']} | {s['점수']} |")
        bad = [
            (at, x)
            for at, xs in v["grounded"].items()
            for x in xs
            if x.get("supported") == "아니오"
        ]
        if bad:
            L += ["", "근거에 없다고 판정된 제안:"] + [
                f"- {at} · {x['text']} — {x['reason']}" for at, x in bad
            ]
    if "gold" in v:
        s = v["gold_score"]
        L += [
            "",
            "## 3. 회차 1(12:50) 시나리오 v3 목표 대비",
            "",
            f"목표 문단 {s['n']}개: 예 {s['예']} · 일부 {s['일부']} · 아니오 {s['아니오']} → 점수 **{s['점수']}**",
            "",
            "| 항목 | 목표 문단 | 판정 | 이유 |",
            "|---|---|---|---|",
        ]
        for iid, xs in v["gold"].items():
            for x in xs:
                L.append(
                    f"| {iid} | {x['gold'][:90]}… | {x.get('covered')} | {x.get('reason', '')} |"
                )
    if "actual" in v:
        s = v["actual_score"]
        L += [
            "",
            "## 4. 백서 실제 기록 대비(회차 시각 뒤의 실제 결정 · 조치를 그 회차 제안이 미리 권했는가)",
            "",
            f"(회차 · 기록) 쌍 {s['n']}개: 예 {s['예']} · 일부 {s['일부']} · 아니오 {s['아니오']} → 점수 **{s['점수']}**",
            "",
            "| 기록 | 시각 | 항목 | 실제 결정 · 조치 | 처음 권한 회차 |",
            "|---|---|---|---|---|",
        ]
        for rec in v["actual_records"]:
            first = rec.get("first") or "—"
            L.append(
                f"| {rec['id']} | {rec['when']} | {rec['item']} | {rec['action'][:70]} | {first} |"
            )
    L += [
        "",
        "판정 모델은 생성 모델과 같은 EXAONE 4.0 32B다(같은 모델이 판정하면 후하게 볼 수 있다).",
        "",
    ]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--ref", type=Path, required=True)
    ap.add_argument("--llm", default="http://127.0.0.1:8031")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    rounds = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(a.runs.glob("round_*.json"))
    ]
    gold = json.loads((a.ref / "scenario_v3_r1_gold.json").read_text(encoding="utf-8"))
    actual = json.loads((a.ref / "actual_records.json").read_text(encoding="utf-8"))["records"]
    v = {"runs": str(a.runs), "auto": automatic(rounds, gold)}
    if not a.no_llm:
        llm = LLM(a.llm, timeout=600)
        jobs = []
        for rd in rounds:
            for it in rd["proposal"] or []:
                jobs.append(("ground", rd, it, None))
        r1 = {it["id"]: it for it in rounds[0]["proposal"] or []}
        for g in gold["items"]:
            if g.get("paragraphs") and g["id"] in r1:
                jobs.append(("gold", rounds[0], r1[g["id"]], g["paragraphs"]))
        for rd in rounds:
            t0 = _mins(rd["round"]["at"])
            later = [r for r in actual if r["at"] and r["recorded"] and _mins(r["at"]) > t0]
            for it in rd["proposal"] or []:
                recs = [r for r in later if r["item"] == it["id"]]
                if recs:
                    jobs.append(("actual", rd, it, recs))

        def run(job):
            kind, rd, it, extra = job
            try:
                if kind == "ground":
                    return job, judge_grounded(llm, rd, it)
                if kind == "gold":
                    return job, judge_gold(llm, it, extra)
                return job, judge_actual(llm, rd, it, extra)
            except Exception as e:  # 판정 실패는 기록만
                return job, [{"error": f"{type(e).__name__}: {e}"}]

        with ThreadPoolExecutor(a.workers) as ex:
            results = list(ex.map(run, jobs))
        v["grounded"], v["gold"], v["actual_raw"] = {}, {}, []
        for (kind, rd, it, _), res in results:
            at = rd["round"]["at"]
            if kind == "ground":
                v["grounded"].setdefault(at, []).extend(x | {"item": it["id"]} for x in res)
            elif kind == "gold":
                v["gold"][it["id"]] = res
            else:
                v["actual_raw"].extend(x | {"round": at, "item": it["id"]} for x in res)
        v["grounded_score"] = {
            at: score([x.get("supported") for x in xs if "error" not in x])
            for at, xs in v["grounded"].items()
        }
        v["gold_score"] = score(
            [x.get("covered") for xs in v["gold"].values() for x in xs if "error" not in x]
        )
        v["actual_score"] = score(
            [x.get("anticipated") for x in v["actual_raw"] if "error" not in x]
        )
        recs = []
        for r in actual:
            hits = [
                x
                for x in v["actual_raw"]
                if x.get("record") == r["id"] and x.get("anticipated") in ("예", "일부")
            ]
            if any(x.get("record") == r["id"] for x in v["actual_raw"]):
                first = min(hits, key=lambda x: _mins(x["round"]), default=None)
                recs.append(
                    r | {"first": f"{first['round']}({first['anticipated']})" if first else None}
                )
        v["actual_records"] = recs
        v["judge_calls"] = len(llm.calls)
        v["actual"] = True
    (a.runs / "verify.json").write_text(
        json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (a.runs / "검증_보고.md").write_text(report(v), encoding="utf-8")
    print(report(v))


if __name__ == "__main__":
    main()
