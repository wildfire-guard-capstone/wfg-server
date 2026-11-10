# ruff: noqa: E501
"""어시스턴트 프롬프트 · JSON 스키마 네 가지.

1. 입력 구분(INTENT) — 규칙으로 가르지 못한 입력만
2. 도구 고르기(PLAN) — 질문 → route + 도구 호출 목록
3. 답변 쓰기(ANSWER) — 도구 결과(상황 값 D번호 · 근거 문서 번호)만으로 문장마다 출처를 달아 답함
4. 정정 해석(CORRECTION) — 자유 문장 → 정정 가능 항목 목록(DB 설계 2.5)

모두 vLLM json_schema로 형식을 묶는다(agent.clients.LLM.chat, 추론 모드 끔).
"""

import json
from typing import Any

from assistant.context import DEPLOYED_LABELS, ITEM_NAMES
from assistant.tools import TOOLS, Fact, ToolResult

ROUTES = ["STATUS", "PROPOSAL", "REGULATION", "OUT_OF_SCOPE"]
MAX_CALLS = 4
MAX_SENTENCES = 5
CHUNK_CHARS = 700

# ── 1. 입력 구분 ────────────────────────────────────────────────────

INTENT_SYSTEM = """산불 대응 시스템의 AI 어시스턴트 입력을 구분합니다.
- QUESTION: 무엇을 묻는 말. 예) 「괴산리 대피 언제까지 해야 해?」 「헬기 몇 대 투입됐어」 「2단계 기준이 뭐야」 「괴산리 대피 완료됐나?」
- CORRECTION: 현장 상황이 이미 바뀌었다고 알리는 말. 예) 「괴산리 대피 완료」 「헬기 2대 추가 투입」 「재난문자 보냈어」 「2단계 발령됐어」 「바람이 세졌어」
- OTHER: 인사 · 잡담 등 둘 다 아닌 말.
상황을 알리면서 묻기도 하면 CORRECTION입니다(묻는 부분은 정정 뒤 다시 물으면 됩니다)."""

INTENT_SCHEMA = {
    "type": "object",
    "properties": {"intent": {"type": "string", "enum": ["QUESTION", "CORRECTION", "OTHER"]}},
    "required": ["intent"],
}


def intent_messages(text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": INTENT_SYSTEM},
        {"role": "user", "content": f"입력: {text}\nJSON으로 답하십시오."},
    ]


# ── 2. 도구 고르기 ──────────────────────────────────────────────────

PLAN_SYSTEM = """당신은 산불 대응 AI 어시스턴트의 도구 선택기입니다. 통합지휘권자의 질문에 답하는 데 필요한 도구를 고릅니다. 답은 쓰지 않습니다.

- route: STATUS(지금 상황 값을 묻는 질문: 면적 · 단계 · 기상 · 자원 · 마을 · 대피소 · 시설 등), PROPOSAL(제안서의 제안 내용 · 판정 이유를 묻는 질문), REGULATION(법령 · 매뉴얼의 기준 · 절차 · 권한을 묻는 질문), OUT_OF_SCOPE(산불 대응과 상관없는 질문).
- calls: 필요한 도구 1~4개. arg는 그 도구에 인자가 있을 때만 씁니다(마을 이름, 항목 코드, 검색할 말). 인자가 없는 도구는 arg를 빈 글로 둡니다.
- 「왜」 「이유」 「근거」 「기준」 「법」 「규정」 「매뉴얼」 「권한」을 물으면 search를 함께 고르고, 검색할 말은 질문의 핵심어로 짧게 씁니다.
- 제안서 항목 내용이나 「무엇을 해야 하나」를 물으면 그 일을 다루는 항목을 item으로 고릅니다(항목 목록 참고).
- 마을 이름이 나오면 village에 그 이름을 넣습니다."""


def tool_lines() -> str:
    return "\n".join(
        f"- {name}: {spec.desc}" + (f" (arg: {spec.arg})" if spec.arg else "")
        for name, spec in TOOLS.items()
    )


def plan_messages(
    text: str, item_code: str | None, items: list[tuple[str, str]], villages: list[str]
) -> list[dict[str, str]]:
    parts = [
        "[도구]\n" + tool_lines(),
        "[이번 제안서 항목]\n"
        + ("\n".join(f"- {c}: {n}" for c, n in items) if items else "(제안서 없음)"),
        "[예측 범위 마을]\n" + (" · ".join(villages) if villages else "(없음)"),
    ]
    if item_code:
        parts.append(
            f"[지정 항목] 사용자가 {item_code} {ITEM_NAMES.get(item_code, '')} 항목을 보며 물었습니다."
        )
    parts.append(f"[질문]\n{text}\n\nJSON으로 답하십시오.")
    return [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "route": {"type": "string", "enum": ROUTES},
            "calls": {
                "type": "array",
                "minItems": 0,
                "maxItems": MAX_CALLS,
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "enum": list(TOOLS)},
                        "arg": {"type": "string", "maxLength": 60},
                    },
                    "required": ["tool", "arg"],
                },
            },
        },
        "required": ["route", "calls"],
    }


# ── 3. 답변 쓰기 ────────────────────────────────────────────────────

ANSWER_SYSTEM = """당신은 산불 현장의 통합지휘권자({reader})를 돕는 AI 어시스턴트입니다. 질문에 [상황 값]과 [근거 문서]만 써서 짧고 정확하게 답합니다.

지킬 것
1. [상황 값]과 [근거 문서]에 있는 내용만 씁니다. 거기 없는 사실 · 수치 · 시각 · 지명 · 기관 · 조치는 지어내지 않습니다.
2. 숫자 · 시각 · 단계 이름 · 지명은 자료에 적힌 그대로 옮깁니다. 더하거나 빼거나 단위를 바꾸지 않습니다. 예측 값은 「~할 것으로 예상됩니다」로 씁니다.
3. 문장마다 출처를 답니다. 상황 값에서 왔으면 data에 그 D번호를, 근거 문서에서 왔으면 docs에 그 문서 번호를 넣습니다. 숫자가 근거 문서에 있으면 그 문서 번호도 넣습니다. 출처가 없는 문장은 쓰지 않습니다.
4. 대응단계 · 위험구역 · 도달 시각 · 대피소 후보 · 지휘권자는 시스템이 판정한 값입니다. 다르게 판정하거나 바꾸자고 하지 않습니다.
5. 상황 값 이름에 「(빠진 정보)」가 붙은 것은 판단하지 않습니다. 질문이 바로 그것을 물었을 때만 「○○는 정보가 없어 판단할 수 없습니다」처럼 한 문장으로 쓰고 그 D번호를 답니다. 질문이 묻지 않은 빠진 정보는 쓰지 않습니다. 다른 값(바람이 약하다 등)으로 빠진 정보를 대신 판단하지 않습니다.
6. sentences는 비우지 않습니다. 묻는 값이 자료에 없어도 질문과 관련해 자료에 있는 값(지금 투입된 대수, 시간별 바람, 불이 닿는 시각, 대피 보고 등)을 먼저 알려 주고, 물은 것 중 없는 것만 위 5처럼 씁니다.
7. 질문에 바로 답하는 문장부터 1~4문장으로 씁니다. 문장은 「~입니다」 「~습니다」로 끝내고, 「무엇을 해야 하나」를 물으면 제안서 문장과 근거 문서에 있는 조치만 「~하십시오」로 씁니다. 질문과 상관없는 내용은 쓰지 않습니다.
8. 이 답변은 상황을 바꾸지 않습니다. 시스템이 문자를 보냈다거나 명령을 내렸다고 쓰지 않습니다.
9. text 안에는 문서 번호 · D번호 · 「상황 값」 「근거 문서」 「(빠진 정보)」 같은 자료 표시 · 내부 코드(P5 등) · JSON 용어를 쓰지 않습니다(출처는 data · docs 칸에만).
10. insufficient에는 질문이 물었는데 자료에 없어 답하지 못한 것만 짧은 명사로 적습니다. 질문이 묻지 않은 것은 적지 않고, 다 답했으면 비웁니다."""


def fact_lines(facts: list[tuple[str, Fact]]) -> str:
    return "\n".join(f"{ref} · {f.label}: {f.value}" for ref, f in facts) or "(없음)"


def chunk_lines(chunks: list[dict[str, Any]]) -> str:
    out = []
    for k, c in enumerate(chunks, 1):
        head = " · ".join(
            x
            for x in (
                c.get("label"),
                c.get("doc_name"),
                c.get("section"),
                "판정 근거" if c.get("origin") == "판정 근거" else "",
            )
            if x
        )
        body = (c.get("text") or "").strip()
        if len(body) > CHUNK_CHARS:
            body = body[:CHUNK_CHARS] + " …"
        out.append(f"[{k}] ({head})\n{body}")
    return "\n\n".join(out) or "(없음)"


def answer_messages(
    reader: str,
    text: str,
    item_code: str | None,
    facts: list[tuple[str, Fact]],
    chunks: list[dict[str, Any]],
    results: list[ToolResult],
) -> list[dict[str, str]]:
    notes = [f"- {r.title}: {r.note}" for r in results if r.note]
    parts = [f"[질문]\n{text}"]
    if item_code:
        parts.append(
            f"[지정 항목] {item_code} {ITEM_NAMES.get(item_code, '')} — 이 항목을 기준으로 답합니다."
        )
    parts += [
        "[상황 값] (D번호 · 이름: 값)\n" + fact_lines(facts),
        "[근거 문서]\n" + chunk_lines(chunks),
    ]
    if notes:
        parts.append("[도구 메모 — 출처로 쓰지 않음]\n" + "\n".join(notes))
    parts.append("위 자료만으로 질문에 답하십시오. JSON으로 답합니다.")
    return [
        {"role": "system", "content": ANSWER_SYSTEM.format(reader=reader or "통합지휘권자")},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def answer_schema(n_facts: int, n_chunks: int) -> dict[str, Any]:
    sentence: dict[str, Any] = {
        "type": "object",
        "properties": {"text": {"type": "string", "maxLength": 220}},
        "required": ["text"],
    }
    if n_facts:
        sentence["properties"]["data"] = {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "enum": [f"D{i}" for i in range(1, n_facts + 1)]},
        }
        sentence["required"].append("data")
    if n_chunks:
        sentence["properties"]["docs"] = {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "integer", "enum": list(range(1, n_chunks + 1))},
        }
        sentence["required"].append("docs")
    return {
        "type": "object",
        "properties": {
            "sentences": {  # 11-10 평가: 묻는 값이 없으면 아는 값까지 비우고 insufficient만 냄 → 자료가 있으면 1개 이상
                "type": "array",
                "minItems": 1 if n_facts or n_chunks else 0,
                "maxItems": MAX_SENTENCES,
                "items": sentence,
            },
            "insufficient": {
                "type": "array",
                "maxItems": 3,
                "items": {"type": "string", "maxLength": 40},
            },
        },
        "required": ["sentences", "insufficient"],
    }


def answer_retry(errors: list[str], sentences: list[dict[str, Any]]) -> dict[str, str]:
    shown = "\n".join(f"- 문장 {i + 1}: {s.get('text')}" for i, s in enumerate(sentences))
    return {
        "role": "user",
        "content": "검사에서 아래 문제가 나왔습니다. 고쳐서 같은 JSON 형식으로 답을 모두 다시 쓰십시오. "
        "자료에서 찾을 수 없는 내용은 빼고 insufficient에 적으십시오.\n"
        + "\n".join(f"- {e}" for e in errors)
        + (f"\n\n[직전 답]\n{shown}" if shown else ""),
    }


# ── 4. 정정 해석 ────────────────────────────────────────────────────

CORRECTION_KINDS = [
    "evacuated",
    "evac_order",
    "pre_evac_done",
    "disaster_sms_sent",
    "helicopter",
    "vehicle",
    "crew",
]
NOT_ALLOWED_KINDS = [
    "stage",
    "weather",
    "expected_hours",
    "injury",
    "fire_line",
    "alert_level",
    "other",
]
GROUP_KEYS = sorted({k for labels in DEPLOYED_LABELS.values() for k in labels})

CORRECTION_SYSTEM = """통합지휘권자가 알린 현장 상황 변화를 정정 항목으로 나눕니다. 입력에 있는 변화를 빠짐없이 나누고, 입력에 없는 변화는 만들지 않습니다.

정정할 수 있는 항목(changes의 kind)
- evacuated: 마을 주민 대피 완료 — village(마을 이름) 필수
- evac_order: 마을에 대피명령을 내림 — village 필수
- pre_evac_done: 마을 사전대피(고령자 등) 완료 — village 필수
- disaster_sms_sent: 재난문자를 보냄
- helicopter · vehicle · crew: 투입한 헬기 · 차량(소방차 · 진화차 · 지휘차) · 인력(진화대 · 공무원 · 소방 · 군 · 경찰) 수
  - count: 입력에 나온 수 그대로(「두 대」 → 2)
  - mode: 「추가 · 더 · 증원 · 도착」이면 add, 「철수 · 복귀 · 빠짐 · 줄」이면 remove, 「총 · 모두 · 현재 · 지금 몇 대(명)」이면 set
  - group: 기관을 말했을 때만 — 헬기 forest(산림청) · local(지자체 · 임차) · fire(소방) · military(군) · police(경찰), 차량 forest_vehicle(진화차 · 지휘차) · fire_truck(소방차) · other, 인력 special_crew(진화대) · forest_officials(공무원) · fire(소방) · military(군) · police(경찰) · other. 모르면 빈 글
- value: 대피 완료 · 명령 · 문자 송출을 했으면 true, 취소했거나 아직이면 false
- 마을 여러 곳이면 마을마다 하나씩 씁니다. emd는 읍면을 말했을 때만 씁니다.

정정할 수 없는 말(not_allowed): 대응단계 발령 · 격상(stage), 바람 · 비 · 기상(weather), 진화예상시간(expected_hours), 부상 · 고립 · 실종(injury), 화선 · 불길 위치(fire_line), 위기경보(alert_level), 그 밖의 상황(other). 이런 말은 changes에 넣지 않고 not_allowed에 원문 조각과 함께 적습니다."""


def correction_messages(text: str, villages: list[str]) -> list[dict[str, str]]:
    names = " · ".join(villages[:200]) if villages else "(없음)"
    return [
        {"role": "system", "content": CORRECTION_SYSTEM},
        {
            "role": "user",
            "content": f"[관할 마을 이름(참고)]\n{names}\n\n[입력]\n{text}\n\nJSON으로 답하십시오.",
        },
    ]


def correction_schema(villages: list[str], emds: list[str]) -> dict[str, Any]:
    village = (
        {"type": "string", "enum": [*dict.fromkeys(villages), ""]}
        if 0 < len(villages) <= 400
        else {"type": "string", "maxLength": 20}
    )
    emd = (
        {"type": "string", "enum": [*dict.fromkeys(emds), ""]}
        if 0 < len(emds) <= 100
        else {"type": "string", "maxLength": 20}
    )
    return {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": CORRECTION_KINDS},
                        "village": village,
                        "emd": emd,
                        "value": {"type": "boolean"},
                        "count": {"type": "integer", "minimum": 0, "maximum": 100000},
                        "mode": {"type": "string", "enum": ["add", "remove", "set"]},
                        "group": {"type": "string", "enum": [*GROUP_KEYS, ""]},
                        "text": {"type": "string", "maxLength": 80},
                    },
                    "required": [
                        "kind",
                        "village",
                        "emd",
                        "value",
                        "count",
                        "mode",
                        "group",
                        "text",
                    ],
                },
            },
            "not_allowed": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": NOT_ALLOWED_KINDS},
                        "text": {"type": "string", "maxLength": 80},
                    },
                    "required": ["kind", "text"],
                },
            },
        },
        "required": ["changes", "not_allowed"],
    }


def loads(raw: str) -> dict[str, Any]:
    """LLM 답 → JSON. 추론 글(… </think>)이 앞에 붙어 있으면 뗀다."""
    body = (raw or "").split("</think>")[-1].strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    return json.loads(body)
