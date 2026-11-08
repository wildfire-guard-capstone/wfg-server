# ruff: noqa: E501
"""규칙 엔진 항목 → 판정 정보(LLM에 주는 것)와 판정 자료(화면에 함께 보이는 목록 · 표).

11/8 사용자 결정: 규칙 엔진은 규칙으로 판단할 수 있는 것만 판정해 넘기고, 무엇을 할지(대응 제안)는
LLM이 근거 검색(RAG) 결과를 참고해 쓴다. 그래서 규칙 엔진 항목에서

- 넘기는 것: finding(판정 요약) · facts(판정 값) · withheld(판단하지 않은 것과 이유) · do_not_state ·
  조립기가 만든 list · table · note(판정 결과를 사람이 읽게 편 것) ·
  다룰 주제(topics = action의 소제목 · 주제 · 쓸 판정 값 이름). 11/8 저녁 사용자 결정: 시나리오 v3처럼 항목마다
  다룰 주제를 알려 준다. 주제는 판정에 따라 달라진다(예: 밤에 닿는 마을이 있을 때만 사전대피 주제)
- 넘기지 않는 것: action의 anchors(주제마다 미리 정한 근거 쪽) · anchor_basis · 조립기의 fixed 문장(정해진 할 일 문장) ·
  targets(내부 ID) · status(영문 코드, facts에 같은 내용이 있음). 문장 내용과 근거는 LLM이 근거 검색으로 정한다

rule_basis(규칙 엔진이 판정에 쓴 근거)는 retrieve.py가 「판정 근거」로 가져온다.
"""

import json
import re
from typing import Any

DATA_BLOCKS = ("list", "table", "note")


def data_blocks(item: dict[str, Any]) -> list[dict[str, Any]]:
    """화면에 제안과 함께 보일 판정 자료(조립기 블록). 표기만 그대로 둔다."""
    return [
        {k: v for k, v in b.items() if k != "writer"}
        for s in item.get("sections", [])
        for b in s.get("blocks", [])
        if b.get("type") in DATA_BLOCKS
    ]


def _plain(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")


def block_lines(block: dict[str, Any]) -> list[str]:
    """판정 자료 블록 → LLM이 읽을 글줄(굵게 표시는 뺀다)."""
    if block["type"] == "note":
        return [_plain(block.get("text", ""))]
    if block["type"] == "list":
        return [f"- {_plain(r.get('text', ''))}" for r in block.get("rows", [])]
    if block["type"] == "table":
        head = block.get("head") or []
        keys = None
        out = [" | ".join(head)] if head else []
        for r in block.get("rows", []):
            if isinstance(r, dict):
                keys = keys or [k for k in r if k not in ("id",)]
                out.append(" | ".join(_plain(str(r.get(k, ""))) for k in keys))
            else:
                out.append(" | ".join(_plain(str(x)) for x in r))
        return out
    return []


def topics_of(item: dict[str, Any]) -> list[dict[str, Any]]:
    """다룰 주제: 규칙 엔진 action 자리의 소제목 · 주제 · 쓸 판정 값(use) · 참조 항목(see). 문장 · 근거 쪽은 뺀다."""
    out = []
    for s in item.get("sections", []):
        for b in s.get("blocks", []):
            if b.get("type") == "action":
                out.append(
                    {
                        "action": b["id"],
                        "section": s.get("title", ""),
                        "topic": b.get("topic", ""),
                        "use": b.get("use", []) or [],
                        "see": b.get("see"),
                    }
                )
    return out


def judgment_of(item: dict[str, Any]) -> dict[str, Any]:
    """LLM에 넘길 판정 정보(항목 하나)."""
    tables = [block_lines(b) for b in data_blocks(item)]
    return {
        "id": item["id"],
        "name": item.get("name", ""),
        "finding": item.get("finding", ""),
        "facts": item.get("facts", {}),
        "withheld": [
            f"{w.get('field')}({w.get('reason')})" for w in item.get("withheld", []) or []
        ],
        "do_not_state": item.get("do_not_state", []) or [],
        "data": [ln for t in tables for ln in t if ln],
        "topics": [
            {"topic": tp["topic"], "section": tp["section"], "use": tp["use"]}
            for tp in topics_of(item)
        ],
    }


def text_of(judgment: dict[str, Any], common: dict[str, Any]) -> str:
    """검사용 글 덩어리(숫자 · 시각 · 이름이 입력에 있는지 볼 때)."""
    return json.dumps({"common": common, "judgment": judgment}, ensure_ascii=False)
