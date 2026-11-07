# ruff: noqa: E501  (지시문 · 항목별 주의는 문장 단위로 둔다)
"""LLM 입력: 호출 1회 = 지시문 + common + item(조립기 블록은 자리만) + 근거 청크 본문(11/7 형식).

- 조립기가 채우는 list · table · note · fixed 블록은 내용을 빼고 자리 표시만 남긴다(LLM이 명단을 다시 쓰지 않게,
  그러나 「아래」 목록이 붙는다는 것은 알게)
- withheld · targets · rule_basis · anchor_basis는 넘기지 않는다. do_not_state만 언급 금지로 넘긴다
- 근거 청크는 앵커(rule_basis ∪ anchor_basis)를 ID로 가져오고, 보충 검색 결과를 덧붙인다
"""

import json
import re
from pathlib import Path
from typing import Any

CATEGORIES = ["즉시", "대기", "협의", "요청"]

SYSTEM = """당신은 산불 통합지휘권자에게 올릴 대응 제안서의 문단을 쓰는 보조자입니다.
규칙 엔진이 판정한 값(finding, facts)과 근거 문서 발췌를 받아, 항목 안의 action 자리마다 문단 하나를 씁니다.

[반드시 지킬 것]
1. 판정을 바꾸지 마십시오. 단계·구역·시각·면적·인원은 finding과 facts에 적힌 그대로 씁니다.
2. 숫자는 finding·facts·근거 발췌에 있는 것만 씁니다. 더하기·빼기·반올림으로 새 숫자를 만들지 마십시오.
3. 마을·시설·기관·도로 이름은 finding·facts에 있는 것만 씁니다.
4. do_not_state에 적힌 내용은 언급하지 마십시오(값을 추측하지도 마십시오).
5. 문단은 1~3문장, 합쇼체로 씁니다. 할 일은 「~하십시오」로 끝내고, 누가 누구에게 무엇을 언제까지 할지 구체적으로 씁니다.
   「등」·「등의」·「등을」은 쓰지 않습니다. 근거 발췌에 「등」이 있어도 옮기지 말고 대상을 나열하거나 빼십시오.
6. 할 일을 말하는 문장은 마침표 바로 앞, 괄호 하나에 근거를 붙입니다.
   맞음: 「… 협의하십시오 [p.73·91].」 「… 인계하십시오 [p.72·114, 시행령 제21조①].」
   틀림: 「… 협의하십시오. [p.73, p.91]」(마침표 뒤)
   근거는 그 action의 anchors에서만 고릅니다. 상황만 설명하는 문장과 anchors가 빈 action에는 근거를 달지 않습니다.
7. action에 see가 있을 때만 문단 끝에 「세부 내용은 S6 참조.」처럼 see 값을 넣어 붙입니다. see가 없으면 「참조」를 쓰지 않습니다.
8. facts에 없는 상황(강풍·야간·급변·피해 발생)을 지어내지 마십시오. 예: 풍속이 3.4 m/s이면 「강풍」이라고 쓰지 않습니다.
   근거 발췌는 할 일과 근거를 고르는 데만 씁니다. 발췌 문장을 그대로 옮겨 붙이거나 topic과 다른 조치(출동·폴리스라인·장비 점검)를 늘어놓지 마십시오.
9. 바로 뒤에 조립기 목록·표(type이 list·table)가 붙는 action은 마을·시설·대피소 이름과 인원을 쓰지 말고 「아래 마을」·「아래 시각까지」처럼 가리킵니다. 목록이 이미 보여 줍니다.
10. 각 action의 topic이 그 문단의 주제이고, use에 적힌 facts 키가 쓸 값입니다. 다른 action의 주제를 끌어오지 마십시오.
11. category는 즉시(지금 할 일)·대기(조건이 되면 할 일, 미리 준비할 일)·협의(산림청장 등 발령권자와 협의)·요청(다른 기관에 요청) 중 하나입니다. 잠재 위험구역(8시간 안) 조치는 대기입니다.

[출력] JSON 하나만: {"paragraphs": [{"action": "<action id>", "category": "<구분>", "text": "<문단>"}]}
action마다 하나씩, 입력 순서대로 씁니다."""


STYLE = json.loads((Path(__file__).parent / "style_examples.json").read_text(encoding="utf-8"))[
    "items"
]
# 문체 예시에만 나오는 고유 이름: 이번 입력에 없으면 검사에서 걸러 낸다
STYLE_NAMES = {"운람사", "서산영덕고속도로", "서의성IC", "안동분기점", "한국도로공사"}
_CITE = re.compile(r"(\[[^\[\]]+\])")
# 예시 문장의 시설 · 도로 이름(지도 자료 이름 목록에 없는 것)
EXAMPLE_PLACES = {
    "신월리",
    "원당리",
    "신안리",
    "괴산리",
    "정자리",
    "철파리",
    "석탑리",
    "안평면",
    "옥산면",
    "의성읍",
    "후죽리",
    "기쁨의집",
    "의성자혜원",
}


def mask_example(text: str, names: set[str] = frozenset()) -> str:
    """문체 예시의 값을 가린다: 숫자 → N, 시각 → HH:MM, 알려진 지명 · 시설명 → ○○(근거 표기는 그대로)."""
    hide = sorted(set(names) | STYLE_NAMES | EXAMPLE_PLACES, key=len, reverse=True)
    out = []
    for i, part in enumerate(_CITE.split(text)):
        if i % 2 == 0:  # 근거 표기 밖
            part = re.sub(r"\d{1,2}:\d{2}", "HH:MM", part)
            part = re.sub(r"(?<![A-Za-z\d])\d+(?:[.,]\d+)?", "N", part)
            for name in hide:
                part = part.replace(name, "○○")
        out.append(part)
    return "".join(out)


# 항목별 주의(11/7 결정 · 매뉴얼 원칙). 판정이 아니라 쓰는 방법만 정한다
HINTS = {
    "S1": "공식 발령 단계는 입력받지 않으므로 단계는 「판정 기준상 ○단계」로 쓰고, 발령·격상은 「산림청장과 협의하십시오」로 씁니다. "
    "요소(면적·풍속·시설)마다 단계가 다르면 그대로 밝힙니다. 지휘권: 100 ha 이상 대형산불은 시·도지사가 통합지휘하고, "
    "넘을 때 즉시 알리고 상황 정보를 인계합니다(시행령 제21조②). 지금 지휘권자가 읽는 사람이면 그 사람에게 쓰고, "
    "이미 인계받은 지휘권자에게는 인계받은 상황을 전제로 씁니다.",
    "S2": "S2-a1은 대상 이름 없이 「진화 우선순위에 따라 아래 순서로 방어하고 보호하십시오」 꼴로 아래 목록을 가리킵니다. "
    "나머지 문단은 그 대상(주택·송전시설·문화유산)을 어떻게 지킬지 씁니다.",
    "S3": "투입 현황과 동원 범위(고시 별표)를 쓰고, 지휘권자가 할 일(가용 자원 집중 투입, 단계에 따른 응원 요청 준비)로 끝냅니다. "
    "헬기·인력을 몇 대·몇 명 더 넣으라는 수치는 넣지 마십시오.",
    "S4": "헬기 운항 가능 여부는 판단하지 않습니다(조립기가 ※ 문장을 붙임). 문단마다 한두 문장만 씁니다. "
    "시간대 문단 = 지금 낮인지 밤인지와 일몰 시각에 맞춘 진화 방식(주간 진화 유지, 일몰 전 야간 진화 체제 준비). "
    "바람 문단 = facts.max의 가장 센 시각·풍향·풍속을 밝히고 그 시간대에 지상 인력·진화장비 중심으로 바꿀 준비. "
    "헬기 재투입 문단 = 바람이 약해지면 바로 다시 넣을 수 있게 이륙 준비 현황과 격납고 지역 풍속 확인.",
    "S5": "투입 인력 안전 책임자에게 전달할 내용을 씁니다. 철수 기준은 규정 별표 3의 조건만 씁니다.",
    "E1": "마을 이름은 아래 목록이 보여 주므로 문단에 쓰지 말고 「리 단위로 아래와 같이 구역을 설정하십시오」 꼴로 씁니다. "
    "「○○ 전체」로 적힌 곳은 리 자료가 없어 읍면 단위로 넣은 곳입니다.",
    "E2": "위험구역은 전 주민 즉시 대피, 잠재 위험구역은 대피 준비와 취약계층 사전대피입니다. 야간에 닿으면 일몰 전 사전대피입니다. "
    "대피명령은 시장·군수의 권한이므로 읽는 사람이 시·도지사이면 「군수에게 지시하십시오」로 씁니다.",
    "E3": "취약시설은 시설별 우선 대피와 이송 준비를 지시합니다. 주야간보호는 운영 여부 확인만 씁니다.",
    "E4": "대피소 배정 인원과 대피 경로는 쓰지 않습니다. 마을·대피소 이름은 아래 목록이 보여 주므로 "
    "대피소 문단(E4-a1·a2)에 쓰지 말고 「아래와 같이 지정하고 안내하십시오」 꼴로 씁니다. 큰 도로 문단만 도로·구간 이름을 씁니다.",
    "E5": "재난문자 문안은 쓰지 않습니다. 송출 요청과 전파 수단만 씁니다.",
}


def strip_item(item: dict[str, Any], fixed: dict[str, dict] | None = None) -> dict[str, Any]:
    """LLM에 넘길 항목: 판정 칸 + 문단 자리. 조립기 블록은 자리 표시만.

    fixed = 조립기가 이미 쓴 action 문단: 다시 쓰지 않게 글을 보여 주고 type을 fixed로 둔다.
    """
    fixed = fixed or {}
    sections = []
    for s in item["sections"]:
        blocks = []
        for b in s["blocks"]:
            if b["type"] == "action" and b["id"] in fixed:
                blocks.append({"type": "fixed", "writer": "조립기", "text": fixed[b["id"]]["text"]})
            elif b["type"] == "action":
                blocks.append({k: v for k, v in b.items() if k != "type"} | {"type": "action"})
            else:
                blocks.append({"type": b["type"], "writer": "조립기(내용은 따로 붙음)"})
        sections.append({"title": s.get("title", ""), "blocks": blocks})
    keep = ("id", "name", "status", "finding", "facts")
    return {
        **{k: item[k] for k in keep if k in item},
        "sections": sections,
        "do_not_state": item.get("do_not_state", []),
    }


def actions(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for s in item["sections"] for b in s["blocks"] if b["type"] == "action"]


def chunk_label(c: dict[str, Any]) -> str:
    if c.get("doc_id") == "SM":
        return f"표준매뉴얼 p.{c['page']}"
    return f"{c['doc_name']} {c.get('section') or ''}".strip()


def evidence_text(chunks: list[dict[str, Any]], extra: list[dict[str, Any]]) -> str:
    parts = []
    for c in chunks:
        parts.append(f"■ [{c['chunk_id']}] {chunk_label(c)}\n{c['text']}")
    for c in extra:
        parts.append(f"■ [보충 {c['chunk_id']}] {chunk_label(c)}\n{c['text']}")
    return "\n\n".join(parts)


def messages(
    common: dict,
    item: dict,
    chunks: list[dict],
    extra: list[dict],
    names: set[str] = frozenset(),
    fixed: dict[str, dict] | None = None,
) -> list[dict[str, str]]:
    fixed = fixed or {}
    body = {"common": common, "item": strip_item(item, fixed)}
    parts = [
        "아래 입력에서 type이 action인 자리만 문단을 쓰십시오(type이 fixed인 문단은 조립기가 이미 썼습니다)."
    ]
    if item["id"] in HINTS:
        parts.append(f"[이 항목에서 주의할 것]\n{HINTS[item['id']]}")
    examples = [p for p in STYLE.get(item["id"], []) if p["action"] not in fixed]
    if examples:
        ex = "\n".join(f"- {mask_example(p['text'], names)}" for p in examples)
        parts.append(
            "[문체 예시] 다른 상황의 목표 문단입니다(값은 N · HH:MM · ○○로 가림). 문장 꼴과 구체성만 참고하십시오. "
            "예시에 있는 대상이라도 이번 facts에 없으면 쓰지 말고, 값·이름·action 구성은 반드시 이번 입력을 따르십시오.\n"
            + ex
        )
    parts.append("[입력]\n" + json.dumps(body, ensure_ascii=False, indent=1))
    parts.append("[근거 발췌]\n" + evidence_text(chunks, extra))
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]


def schema(item: dict[str, Any], only: set[str] | None = None) -> dict[str, Any]:
    ids = [a["id"] for a in actions(item) if only is None or a["id"] in only]
    return {
        "type": "object",
        "properties": {
            "paragraphs": {
                "type": "array",
                "minItems": len(ids),
                "maxItems": len(ids),
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ids},
                        "category": {"type": "string", "enum": CATEGORIES},
                        "text": {"type": "string"},
                    },
                    "required": ["action", "category", "text"],
                },
            }
        },
        "required": ["paragraphs"],
    }


def retry_message(
    errors: list[str], paras: list[dict[str, Any]], names: set[str] = frozenset()
) -> dict[str, str]:
    bad = {e.split(":")[0] for e in errors}
    shown = "\n".join(
        f"- {p.get('action')}: {p.get('text')}" for p in paras if p.get("action") in bad
    )
    # 목록 앞 문단 이름 문제: 같은 action의 목표 문단 꼴(값 가림)을 보여 준다
    forms = []
    for e in errors:
        aid = e.split(":")[0]
        if "아래 목록이" in e:
            ex = [p for ps in STYLE.values() for p in ps if p["action"] == aid]
            if ex:
                forms.append(f"- {aid}: {mask_example(ex[0]['text'], names)}")
    return {
        "role": "user",
        "content": "검사에서 아래 문제가 나왔습니다. 문제를 고쳐 같은 JSON 형식으로 모든 action을 다시 쓰십시오.\n"
        + "\n".join(f"- {e}" for e in errors)
        + (f"\n\n[문제가 있던 문단]\n{shown}" if shown else "")
        + ("\n\n[이렇게 이름 없이 쓰십시오]\n" + "\n".join(dict.fromkeys(forms)) if forms else ""),
    }
