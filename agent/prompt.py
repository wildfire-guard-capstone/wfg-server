# ruff: noqa: E501  (지시문은 문장 단위로 둔다)
"""LLM 입력(11/8 재설계): 호출 1회 = 항목 하나 = 지시문 + 읽는 사람 · 상황 + 규칙 엔진 판정 + 근거 발췌.

- 규칙 엔진은 판정과 다룰 주제만 넘긴다(judgment.py). 문장 · 주제별 근거 쪽은 정하지 않는다.
- 무엇을 제안할지는 LLM이 근거 발췌(retrieve.py)에서 고르고, 제안마다 주제(topic) · 근거 청크 ID(evidence) ·
  근거 문장(quote)을 단다. 근거 표기([p.79])는 cite.py가 evidence로 만든다.
- 출력 형식은 vLLM json_schema로 묶는다: topic은 다룰 주제 중에서, evidence는 이번에 준 청크 ID 중에서만 고를 수 있다.
- 문단 꼴 예시(11/8 저녁 사용자 결정): 시나리오 v3 회차 1 목표 문단(style_examples.json = wfg-docs LLM-출력-예시)을
  같은 항목에 한해 값(숫자 · 시각 · 이름)을 가려 보여 준다. 문단의 밀도 · 구조를 맞추려는 것이다. 회차 1과 겹치므로
  회차 1 검증은 참고로만 보고 회차 2 ~ 4를 주 검증으로 쓴다.
"""

import json
import re
from pathlib import Path
from typing import Any

CATEGORIES = ["즉시", "대기", "협의", "요청"]
MAX_PROPOSALS = 5
MAX_EVIDENCE = 4

SYSTEM = """당신은 산불 통합지휘권자(읽는 사람)에게 올릴 대응 제안서의 한 항목을 쓰는 보조자입니다.
입력은 두 가지입니다.
- [규칙 엔진 판정]: 규칙으로 정한 값(단계 · 지휘권자 · 구역 · 마을 · 도달 시각 · 면적 · 인원 · 자원 · 바람 · 일몰)과 그 목록 · 표입니다. 확정된 사실로 다룹니다.
- [근거 발췌]: 표준매뉴얼 · 법령 · 규정에서 찾은 원문입니다. 무엇을 해야 하는지는 여기서 찾습니다.

[할 일]
이번 판정 상황에서 읽는 사람이 지금 또는 곧 해야 할 조치를 근거 발췌에서 골라 이 항목의 대응 제안으로 씁니다.
1. [다룰 주제]마다 제안을 하나씩 씁니다(topic에 주제 이름). 주제의 소제목과 use(쓸 판정 값)를 보고, 그 주제에 맞는 조치를 근거 발췌에서 찾습니다. 먼저 근거 발췌 전체를 훑어 이번 판정 상황(시각 · 바람 · 대상)에 맞는 문장을 고르십시오.
   조치는 근거 발췌에 적힌 것만 제안합니다. 제안마다 그 조치가 적힌 발췌 ID를 evidence에 넣고, 그 발췌에서 조치를 적은 문장을 옮긴 부분(20~80자)을 quote에 넣습니다. 발췌에 없는 조치 · 기관 · 수치는 쓰지 않습니다.
2. 판정 상황에 맞는 조치만 고릅니다. 판정에 없는 상황(강풍 · 야간 · 피해 발생 · 통신 두절)이나 판정에 없는 대상(도로 · 시설 · 마을)을 가정해 「…할 경우」로 제안하지 않습니다. 이 항목 범위 밖의 일은 [다른 항목]이 다루므로 쓰지 않습니다.
   지금 할 일과 앞으로의 일을 구분합니다. 판정이 「5시간 뒤 2단계」 「지휘권자 의성군수 → 경상북도지사」 「19:20 ~ 20:20 닿을 수 있음」처럼 앞으로의 일이면, 그 시각 · 조건을 밝히고 「…까지(…되면) …하십시오」로 씁니다(구분 대기). 아직 일어나지 않은 일을 지금 하라고 쓰지 않습니다.
3. 판정 값은 바꾸지 않습니다. 단계 · 시각 · 면적 · 인원 · 이름은 판정에 적힌 그대로 쓰고, 더하기 · 빼기 · 반올림으로 새 숫자를 만들지 않습니다. 숫자 · 시각 · 마을 · 시설 · 기관 이름은 판정과 발췌에 있는 것만 씁니다.
4. withheld(판단하지 않은 것)와 do_not_state에 적힌 값은 추측해 쓰지 않습니다.
5. 구체적으로 씁니다: 누가(읽는 사람, 또는 읽는 사람이 지시 · 요청할 기관) 무엇을 어디에(마을 · 시설 · 구간) 언제까지(판정의 시각) 하는지. 판정에 대상 이름과 시각이 있으면 문장에 넣습니다. 명단이 길면 「아래 목록의 마을」처럼 판정 자료의 목록을 가리켜도 됩니다.
6. 읽는 사람의 권한에 맞게 씁니다. [읽는 사람의 권한]에 적힌 대로 씁니다.
7. 제안서는 읽는 사람에게 직접 권하는 글입니다. 할 일은 반드시 읽는 사람에게 「~하십시오」로 씁니다.
   - 읽는 사람이 직접 할 일: 「…을 발령하십시오」 「…을 산림청장과 협의하십시오」
   - 소속 부서 · 하급 기관이 할 일: 「○○에게 …하도록 지시하십시오」
   - 다른 기관에 맡길 일: 「○○에 …을 요청하십시오」
   틀림: 「○○과장은 …하라」 「○○군수는 …해야 합니다」 「…조치를 취해야 합니다」(제삼자에게 시키거나 서술로 끝냄)
   읽는 사람을 주어로 쓰지 않습니다(틀림: 「의성군수는 …하십시오」 → 맞음: 「…하십시오」).
   제안 하나는 2~4문장, 합쇼체입니다. [문단 꼴 예시]처럼 먼저 판정의 상황(시각 · 면적 · 풍속 · 대상 이름 · 인원 · 동수)을 밝히고, 이어서 할 일을 구체적으로 씁니다(상황 문장이 붙는 주제는 11번). 할 일을 부정하는 문장(「~하지 않습니다」)과 「등」은 쓰지 않습니다. 근거 표기([p.79] 같은 괄호)는 쓰지 않습니다(evidence로 붙습니다).
   「보호 조치를 취하십시오」 「철저히 하십시오」처럼 방법이 빠진 말은 쓰지 말고, 근거 발췌에 적힌 구체적인 방법(무엇을 · 어떻게)을 씁니다. 발췌의 제목 · 일반 문구를 그대로 옮기지 말고 이번 상황(대상 · 시각)에 맞게 씁니다.
8. 다룰 주제가 없으면 인명 보호와 급한 일부터 2~3개 씁니다. 제안마다 서로 다른 조치를 씁니다. 같은 조치를 대상(마을 · 시설)만 바꿔 여러 제안으로 나누지 말고, 대상을 한 제안에 모아 씁니다(「괴산리 · 석탑리 주민을 …」).
9. category: 즉시(지금 할 일) · 대기(조건이 되면 할 일, 미리 준비할 일) · 협의(산림청장 등 발령권자와 협의) · 요청(다른 기관에 요청). 잠재 위험구역(5~8시간) 조치는 대기입니다.
10. [이 항목 작성 지침]은 이번 상황(규모 · 단계 · 시간대 · 읽는 사람)에서 이 항목에 무엇을 쓰고 무엇을 쓰지 않을지 알려 줍니다. 「쓸 것」은 주제별로 따르고, 「덧붙일 제안」은 근거 발췌에 그 조치가 있을 때만 그 주제로 하나 씁니다(없으면 쓰지 않음). 「쓰지 않을 것」은 쓰지 않습니다. 지침의 조치도 근거 발췌에 적힌 것만 씁니다.
11. [다룰 주제]에 「상황 문장」이 적힌 주제는 판정 값으로 코드가 쓴 그 문장이 제안 앞에 그대로 붙습니다. 그 주제의 text에는 상황 문장 뒤에 이어질 할 일 문장(1~2문장)만 씁니다. 상황 문장의 값(숫자 · 시각 · 면적 · 동수 · 범위)을 되풀이하지 말고, 상황 문장에 없는 숫자를 쓰지 않습니다(「그때」 「그 전에」처럼 상황 문장을 가리켜 씁니다).

[출력] JSON 하나만: {"proposals": [{"topic": "<다룰 주제>", "category": "<구분>", "text": "<제안>", "evidence": ["<발췌 ID>", ...], "quote": "<발췌 원문>"}]}"""

STYLE = json.loads((Path(__file__).parent / "style_examples.json").read_text(encoding="utf-8"))[
    "items"
]
# 예시에만 나오는 고유 이름(지도 자료 이름과 함께 가린다)
STYLE_NAMES = {
    "운람사",
    "서산영덕고속도로",
    "서의성IC",
    "안동분기점",
    "한국도로공사",
    "기쁨의집",
    "의성자혜원",
}
_CITE = re.compile(r"\s*\[[^\[\]]+\]")


def mask_example(text: str, names: set[str] = frozenset()) -> str:
    """예시 문단의 값을 가린다: 근거 표기 지움, 시각 → HH:MM, 숫자 → N, 지명 · 시설명 → ○○."""
    text = _CITE.sub("", text)
    text = re.sub(r"\d{1,2}:\d{2}", "HH:MM", text)
    text = re.sub(r"(?<![A-Za-z\d])\d+(?:[.,]\d+)?", "N", text)
    for name in sorted(set(names) | STYLE_NAMES, key=len, reverse=True):
        text = text.replace(name, "○○")
    return text


def action_part(text: str) -> str:
    """예시 문단에서 할 일 문장(「~하십시오」가 든 문장)만 남긴다."""
    sents = re.split(r"(?<=[.다])\s+", _CITE.sub("", text).strip())
    return " ".join(s for s in sents if "십시오" in s)


def examples_of(
    item_id: str, names: set[str] = frozenset(), action_only: set[str] = frozenset()
) -> list[str]:
    """같은 항목의 v3 목표 문단(값을 가림). action_only의 주제는 상황 문장을 코드가 붙이므로 할 일 문장만 보인다."""
    out = []
    for p in STYLE.get(item_id, []):
        text = action_part(p["text"]) if p.get("action") in action_only else p["text"]
        if text:
            out.append(mask_example(text, names))
    return out


# 항목 범위: 이 항목이 다루는 것(검색 질의로도 쓴다)과 지킬 것(11/7 결정 · 근거 부족 규칙). 할 일을 정하지는 않는다
ITEM_SCOPE = {
    "S1": (
        "산불 확산대응 단계 판정과 발령 · 격상 협의, 통합지휘권자와 지휘권 인계",
        "공식 발령 단계는 입력받지 않으므로 단계는 「판정 기준상 ○단계」로 씁니다. 지금 단계와 5시간 뒤 단계, 지금 지휘권자와 다음 지휘권자를 구분해 지금 할 일과 기준을 넘을 때 할 일을 나눠 씁니다.",
    ),
    "S2": (
        "산불 진화 우선순위와 인명 · 주택 · 국가기간시설(송전시설) · 국가유산 보호 조치",
        "판정의 보호 대상(마을 주택 · 송전선 · 국가유산)마다 무엇으로 어떻게 지킬지 씁니다. 주민 대피는 E2가 다룹니다.",
    ),
    "S3": (
        "진화자원(헬기 · 인력 · 장비) 투입 현황과 대응 단계별 진화자원 동원 범위, 응원 요청",
        "투입 현황과 지금 · 5시간 뒤 동원 범위는 상황 문장으로 붙습니다. 헬기 · 인력을 몇 대 · 몇 명 더 넣으라는 수치는 쓰지 않습니다(산정 기준 없음).",
    ),
    "S4": (
        "주간 · 야간 시간대와 바람에 따른 진화 방식, 공중 진화와 지상 진화",
        "판정의 일몰 시각과 최대 풍속 시각을 기준으로 시간대별 진화 방식을 씁니다. 헬기 운항 가능 여부는 판단하지 않습니다(운항 기준 없음).",
    ),
    "S5": (
        "진화 인력 안전 관리: 안전장구, 위치 추적, 안전 교육, 위험 요소 전달, 철수 기준, 통신",
        "판정의 주 확산 방향과 최대 풍속을 현장에 전달할 위험 요소로 씁니다.",
    ),
    "S6": ("유관기관 동원 · 협조 요청과 재난안전대책본부 · 산불현장 통합지휘본부 운영", ""),
    "E1": ("주민 대피를 위한 위험구역(5시간) · 잠재 위험구역(8시간) 설정", ""),
    "E2": ("주민 대피 대상 · 순서 · 시점, 대피명령, 야간 사전대피, 안전취약계층 우선 대피", ""),
    "E3": ("요양원 · 요양병원 · 장애인 시설 같은 취약시설 거주자의 대피와 이송", ""),
    "E4": (
        "대피소 지정과 안내, 산불이 도로로 번질 때 교통 통제 · 우회도로 지정 · 도로 이용객 대피",
        "대피소별 배정 인원과 대피 경로는 쓰지 않습니다.",
    ),
    "E5": ("재난문자 · 재난방송 · 마을방송으로 대피 정보 전파", "재난문자 문안은 쓰지 않습니다."),
    "E6": ("유관기관에 할 요청: 거주자 이송, 교통 통제, 주민대피 지원, 재난방송 송출", ""),
}


def scope_of(item_id: str) -> str:
    return ITEM_SCOPE.get(item_id, ("", ""))[0]


def evidence_text(chunks: list[dict[str, Any]]) -> str:
    parts = []
    for c in chunks:
        where = f"{c.get('doc_name', '')} {c.get('section') or ''}".strip()
        parts.append(
            f"■ {c['chunk_id']} ({c.get('label', '')} · {where} · {c.get('origin', '')})\n{c['text']}"
        )
    return "\n\n".join(parts)


UPPER = ("도지사", "특별시장", "광역시장", "특별자치", "산림청장")


def head_of(sigungu: str) -> str:
    """의성군 → 의성군수, ○○구 → ○○구청장, ○○시 → ○○시장."""
    if sigungu.endswith("군"):
        return sigungu + "수"
    if sigungu.endswith("구"):
        return sigungu + "청장"
    return sigungu + "장"


def authority(common: dict) -> str:
    """읽는 사람의 권한 줄(재난안전법 제40조: 대피명령은 시장 · 군수 · 구청장). 판정이 아니라 법이 정한 권한이다."""
    reader = common.get("reader", "")
    head = (
        head_of((common.get("incident") or {}).get("sigungu", ""))
        if (common.get("incident") or {}).get("sigungu")
        else "시장 · 군수"
    )
    if any(u in reader for u in UPPER):
        return (
            f"읽는 사람은 {reader}입니다. 대피명령 · 대피 권고 · 위험구역 설정 · 주민대피는 {head}의 권한이므로 "
            f"「{head}에게 …하도록 지시하십시오」로 씁니다. 「{reader}는 …」처럼 제삼자로 쓰지 않습니다."
        )
    return (
        f"읽는 사람은 {reader}입니다. {reader}가 직접 할 일은 「…하십시오」로, 소속 부서 · 읍면이 할 일은 "
        f"「○○에게 …하도록 지시하십시오」로 씁니다. 「{reader}는 …해야 합니다」처럼 제삼자로 쓰지 않습니다."
    )


def messages(
    common: dict,
    judgment: dict,
    chunks: list[dict],
    names: set[str] = frozenset(),
    guide: str = "",
    situation_line: str = "",
    situations: dict[str, str] | None = None,
    action_only: set[str] = frozenset(),
) -> list[dict[str, str]]:
    """situations = {주제: 상황 문장}(situate.py, 코드가 판정으로 씀 — LLM은 할 일 문장만).
    action_only = 상황 문장이 붙는 주제의 action ID: 그 주제의 문단 꼴 예시는 할 일 문장만 보인다."""
    situations = situations or {}
    scope, rule = ITEM_SCOPE.get(judgment["id"], ("", ""))
    head = f"이번 항목: {judgment['id']} {judgment.get('name', '')}"
    if scope:
        head += f"\n이 항목이 다루는 것: {scope}"
    if rule:
        head += f"\n이 항목에서 지킬 것: {rule}"
    situation = {
        k: common.get(k)
        for k in ("reader", "incident", "proposal", "current_stage", "commander")
        if k in common
    }
    judged = {k: judgment.get(k) for k in ("finding", "facts", "withheld", "do_not_state")}
    topics = judgment.get("topics") or []
    others = " · ".join(
        f"{k} {v[0].split(',')[0]}" for k, v in ITEM_SCOPE.items() if k != judgment["id"]
    )
    parts = [
        head,
        "[다른 항목 — 이 항목에 쓰지 않음]\n" + others,
        "[읽는 사람의 권한]\n" + authority(common),
        "[읽는 사람 · 상황]\n"
        + json.dumps(situation, ensure_ascii=False)
        + (f"\n이번 상황: {situation_line}" if situation_line else ""),
        "[규칙 엔진 판정]\n" + json.dumps(judged, ensure_ascii=False),
    ]
    if topics:
        parts.append(
            "[다룰 주제 — 주제마다 제안 하나]\n"
            + "\n".join(
                f"- {tp['topic']} (소제목 {tp['section'] or '없음'} · 쓸 판정 값 {', '.join(tp['use']) or '—'})"
                + (
                    f"\n  상황 문장(제안 앞에 그대로 붙음 — text에는 이어질 할 일 문장만): 「{situations[tp['topic']]}」"
                    if tp["topic"] in situations
                    else ""
                )
                for tp in topics
            )
        )
    if guide:
        parts.append("[이 항목 작성 지침 — 이번 상황에 맞춘 것]\n" + guide)
    ex = examples_of(judgment["id"], names, action_only)
    if ex:
        parts.append(
            "[문단 꼴 예시] 같은 항목의 목표 문단입니다(다른 시각 · 상황, 값은 N · HH:MM · ○○로 가림). 문장 구조와 구체성(상황 → 할 일)만 따르고, "
            "값 · 이름 · 조치는 반드시 이번 판정과 근거 발췌에서 가져오십시오. 예시에만 있는 대상은 쓰지 않습니다.\n"
            + "\n".join(f"- {e}" for e in ex)
        )
    if judgment.get("data"):
        parts.append(
            "[판정 자료 — 제안 아래에 목록 · 표로 함께 보입니다]\n" + "\n".join(judgment["data"])
        )
    parts.append("[근거 발췌]\n" + evidence_text(chunks))
    parts.append("위 판정 상황에 맞는 대응 제안을 근거 발췌에서 골라 JSON으로 쓰십시오.")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]


def schema(
    evidence_ids: list[str],
    topics: list[str] | None = None,
    extras: list[str] | None = None,
) -> dict[str, Any]:
    """제안 목록 json_schema. extras = None이면 예전처럼 「그 밖의 제안」 한 칸을 더 열고,
    리스트면(작성 지침 사용) 이번 상황에 켜진 덧붙일 주제만 연다(빈 리스트 = 다룰 주제만)."""
    topics = list(dict.fromkeys(t for t in (topics or []) if t))
    opt = (
        ["그 밖의 제안"]
        if extras is None
        else list(dict.fromkeys(e for e in extras if e and e not in topics))
    )
    n_min, n_max = (len(topics), len(topics) + len(opt)) if topics else (1, MAX_PROPOSALS)
    item_props: dict[str, Any] = {}
    if topics:
        item_props["topic"] = {"type": "string", "enum": topics + opt}
    return {
        "type": "object",
        "properties": {
            "proposals": {
                "type": "array",
                "minItems": n_min,
                "maxItems": n_max,
                "items": {
                    "type": "object",
                    "properties": {
                        **item_props,
                        "category": {"type": "string", "enum": CATEGORIES},
                        "text": {"type": "string"},
                        "quote": {"type": "string"},
                        "evidence": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": MAX_EVIDENCE,
                            "items": {"type": "string", "enum": evidence_ids},
                        },
                    },
                    "required": (["topic"] if topics else [])
                    + ["category", "text", "evidence", "quote"],
                },
            }
        },
        "required": ["proposals"],
    }


def retry_message(errors: list[str], proposals: list[dict[str, Any]]) -> dict[str, str]:
    shown = "\n".join(f"- 제안 {i + 1}: {p.get('text')}" for i, p in enumerate(proposals))
    return {
        "role": "user",
        "content": "검사에서 아래 문제가 나왔습니다. 문제를 고쳐 같은 JSON 형식으로 제안을 모두 다시 쓰십시오. "
        "근거 발췌에서 찾을 수 없는 조치는 빼십시오.\n"
        + "\n".join(f"- {e}" for e in errors)
        + (f"\n\n[직전 제안]\n{shown}" if shown else ""),
    }
