# ruff: noqa: E501  (지시문은 문장 단위로 둔다)
"""LLM 입력(11/8 재설계, 11/17 네 단계): 호출 1회 = 항목 하나 = 지시문 + 읽는 사람 · 상황 + 규칙 엔진 판정 + 근거 발췌.

- 11/17 사용자 결정: 한 항목을 상황 → 상태 → 판단 → 제안 네 문단으로 쓴다(문서 「대응 제안 4단계 구조와 근거」).
  네 문단 모두 LLM이 쓴다. 상황 · 상태 · 판단은 판정 값만으로 쓰는 서술문, 제안은 근거 발췌에서 고른 할 일이다.
  판단에 필요한 계산(대피소 수용 비교처럼 더하기 · 빼기)은 calc.py가 판정 값으로 미리 해서 [판단 계산 값]으로 준다.
  S1 ~ S3은 situate.py가 판정 값으로 만든 문장을 [판정 값 문장]으로 함께 준다(값을 옮겨 쓰다 틀리지 않게).
- 규칙 엔진은 판정과 다룰 주제만 넘긴다(judgment.py). 문장 · 주제별 근거 쪽은 정하지 않는다.
- 무엇을 제안할지는 LLM이 근거 발췌(retrieve.py)에서 고르고, 제안마다 주제(topic) · 근거 청크 ID(evidence) ·
  근거 문장(quote)을 단다. 근거 표기([p.79])는 cite.py가 evidence로 만든다.
- 출력 형식은 vLLM json_schema로 묶는다: situation · status · judgment(빈 글 가능) · proposals 순서.
  topic은 다룰 주제 중에서, evidence는 이번에 준 청크 ID 중에서만 고를 수 있다.
- 문단 꼴 예시(style_examples.json): 문서 9장의 회차 1(12:50) 네 단계 예시를 이름 · 숫자 · 시각을 가려
  같은 항목에 한해 보여 준다. 회차 1과 겹치므로 회차 1 결과는 예시를 닮기 쉽다(검증은 다른 회차로도 볼 것).
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

[쓰는 순서: 상황 → 상태 → 판단 → 제안]
한 항목을 네 문단으로 씁니다. 문단에 「상황:」 같은 이름을 붙이지 말고, 읽는 사람이 위에서 아래로 이어 읽을 수 있는 줄글로 씁니다.
- situation(상황): 불 쪽 — 무엇이 언제 닥치는가. 대상(마을 · 시설 · 도로 · 송전선 이름)과 불이 닿는 시각, 면적 · 바람의 변화를 1~3문장으로 씁니다.
- status(상태): 우리 쪽 — 지금 무엇이 있는가. 할 일을 바꾸는 조건(인원 · 시설 종류 · 대피소 거리와 수용 인원 · 투입 자원 · 지휘권자 · 관할 기관)과 정보가 없는 것을 1~4문장으로 씁니다.
- judgment(판단): 상황과 상태를 맞대 본 결과 — 무엇이 모자라거나 몰리거나 겹치거나 가장 급한가를 수치와 함께 1~3문장으로 씁니다. 판단할 거리가 없으면 빈 글("")로 둡니다.
- proposals(제안): 판단에서 드러난 문제를 메우는 할 일입니다. 아래 [제안 쓰는 법]을 따릅니다. 제안들이 이어져 네 번째 문단이 됩니다.
상황 · 상태 · 판단은 「~입니다 · ~습니다」로 끝나는 서술문으로 쓰고, 할 일(「~하십시오」)을 넣지 않습니다. 제안에는 상황 · 상태 · 판단 문장을 되풀이하지 않고 할 일에 필요한 대상 이름과 기한만 넣습니다.
- 상황은 이 항목의 대상에 대해서만 씁니다. 산불 전체의 발생 시각 · 위치 · 면적 · 바람([읽는 사람 · 상황]의 「이번 상황」)은 S1(면적) · S4 · S5(바람)만 씁니다. 다른 항목은 그 항목의 대상(마을 · 시설 · 도로 · 송전선)에 불이 닿는 시각을 씁니다.
- 산불을 부를 때는 「산불」 또는 입력의 산불 이름(incident.name)만 씁니다. 입력에 없는 산 이름 · 지명을 만들지 않습니다.
- 시각과 값은 판정에서 함께 적힌 짝으로만 씁니다(틀림: 지금 면적을 다른 시각과 묶어 「14:50 무렵 48.5 ha」).
- 「정보가 없습니다」는 withheld에 있는 것 가운데 이 항목의 할 일을 바꾸는 것만 한 번 씁니다. 다른 항목이 다루는 정보(대피소 · 투입 자원 · 병원)가 없다고 쓰지 않고, 판정 자료(목록 · 표)에 있는 것을 없다고 쓰지 않습니다.
- 문단 앞에 「상황:」 「(판단)」 같은 이름이나 기호를 붙이지 않습니다.

[값 규칙 — 네 문단 모두]
1. 판정 값은 바꾸지 않습니다. 단계 · 시각 · 면적 · 인원 · 이름은 판정에 적힌 그대로 씁니다. [판정 값 문장]이 있으면 그 값과 표현을 그대로 옮겨 써도 됩니다.
2. 새 숫자를 만들지 않습니다. 더하기 · 빼기 · 비율 같은 계산이 필요한 판단은 [판단 계산 값]에 적힌 값만 씁니다. [판단 계산 값]의 문장은 숫자의 짝(어느 대피소에 몇 명이 몰려 몇 명이 넘치는지)을 바꾸지 말고 거의 그대로 옮겨 씁니다. 거기에 없으면 계산하지 말고 판정 값끼리 맞대어 씁니다(예: 「3,695명 가운데 2,623명이 용기리에 있습니다」).
3. withheld(판단하지 않은 것)와 do_not_state의 값은 추측하지 않습니다. 정할 수 없는 것은 「~는 정보가 없습니다」로 밝힙니다.
4. 판정에 없는 상황(강풍 · 야간 · 피해 발생 · 통신 두절)이나 대상(도로 · 시설 · 마을)을 가정하지 않습니다. 이 항목 범위 밖의 일은 [다른 항목]이 다루므로 쓰지 않습니다.
5. 지금의 일과 앞으로의 일을 구분합니다. 「5시간 뒤 2단계」 「지휘권자 의성군수 → 경상북도지사」 「19:20 ~ 20:20 닿을 수 있음」처럼 앞으로의 일은 「~할 수 있습니다」로 쓰고, 제안은 「…까지(…되면) …하십시오」로 씁니다(구분 대기). 아직 일어나지 않은 일을 지금 하라고 쓰지 않습니다.

[제안 쓰는 법]
6. [다룰 주제]마다 제안을 하나씩 씁니다(topic에 주제 이름). 주제의 소제목과 use(쓸 판정 값)를 보고 그 주제에 맞는 조치를 근거 발췌에서 찾습니다. 다룰 주제가 없으면 인명 보호와 급한 일부터 씁니다. 제안 하나는 1~3문장, 합쇼체입니다.
7. 조치는 근거 발췌에 적힌 것만 제안합니다. 제안마다 그 조치가 적힌 발췌 ID를 evidence에 넣고, 그 발췌에서 조치를 적은 문장을 옮긴 부분(20~80자)을 quote에 넣습니다. 발췌에 없는 조치 · 기관 · 수치는 쓰지 않습니다. 발췌의 제목 · 일반 문구를 그대로 옮기지 말고 이번 상황의 대상 · 시각에 맞게 씁니다.
8. 구체적으로 씁니다: 누가(읽는 사람, 또는 읽는 사람이 지시 · 요청할 기관) 무엇을 어디에(마을 · 시설 · 구간) 언제까지(판정의 시각) 하는지. 판단에서 모자람 · 몰림이 드러났으면 그것을 메우는 다음 안(두 번째 후보 · 추가 지정 · 먼저 할 대상)을 씁니다. 정보가 없어 정할 수 없는 것은 지어내지 말고 「~를 확인하십시오」처럼 확인할 일로 씁니다.
9. 할 일은 반드시 읽는 사람에게 「~하십시오」로 씁니다.
   - 읽는 사람이 직접 할 일: 「…을 발령하십시오」 「…을 산림청장과 협의하십시오」
   - 소속 부서 · 하급 기관이 할 일: 「○○에게 …하도록 지시하십시오」
   - 다른 기관에 맡길 일: 「○○에 …을 요청하십시오」
   틀림: 「○○과장은 …하라」 「○○군수는 …해야 합니다」 「…조치를 취해야 합니다」(제삼자에게 시키거나 서술로 끝냄). 읽는 사람을 주어로 쓰지 않습니다(틀림: 「의성군수는 …하십시오」).
   할 일을 부정하는 문장(「~하지 않습니다」), 「등」, 「보호 조치를 취하십시오」 「철저히 하십시오」처럼 방법이 빠진 말은 쓰지 않습니다. 근거 표기([p.79] 같은 괄호)는 쓰지 않습니다(evidence로 붙습니다).
10. 읽는 사람의 권한에 맞게 씁니다. [읽는 사람의 권한]에 적힌 대로 씁니다.
11. 제안마다 서로 다른 조치를 씁니다. 같은 조치를 대상(마을 · 시설)만 바꿔 여러 제안으로 나누지 말고, 대상을 한 제안에 모아 씁니다(「괴산리 · 석탑리 주민을 …」).
12. category: 즉시(지금 할 일) · 대기(조건이 되면 할 일, 미리 준비할 일) · 협의(산림청장 등 발령권자와 협의) · 요청(다른 기관에 요청). 잠재 위험구역(5~8시간) 조치는 대기입니다.
13. [이 항목 작성 지침]은 이번 상황(규모 · 단계 · 시간대 · 읽는 사람)에 맞춘 것입니다. 「판단에서 볼 것」은 judgment에 씁니다. 「쓸 것」은 주제별 제안에 따릅니다. 「덧붙일 제안」은 근거 발췌에 그 조치가 있을 때만 그 주제로 하나 씁니다. 「쓰지 않을 것」은 쓰지 않습니다. 지침의 조치도 근거 발췌에 적힌 것만 씁니다.

[출력] JSON 하나만: {"order": "상황 → 상태 → 판단 → 제안", "situation": "<상황 문단>", "status": "<상태 문단>", "judgment": "<판단 문단 또는 빈 글>", "proposals": [{"topic": "<다룰 주제>", "category": "<구분>", "text": "<제안>", "evidence": ["<발췌 ID>", ...], "quote": "<발췌 원문>"}]}"""

FACT_STEPS = ("situation", "status", "judgment")
ORDER = "상황 → 상태 → 판단 → 제안"  # json_schema 첫 칸(고정값)
STEP_NAMES = {"situation": "상황", "status": "상태", "judgment": "판단", "proposals": "제안"}

STYLE = json.loads((Path(__file__).parent / "style_examples.json").read_text(encoding="utf-8"))[
    "items"
]
# 예시는 만들 때 이름을 가려 두었다(masked). 여기서는 이번 지도 자료 이름과 남은 숫자 · 시각만 한 번 더 가린다
STYLE_NAMES: set[str] = set()
_CITE = re.compile(r"\s*\[[^\[\]]+\]")


def mask_example(text: str, names: set[str] = frozenset()) -> str:
    """예시 문단의 값을 가린다: 근거 표기 지움, 시각 → HH:MM, 숫자 → N, 지명 · 시설명 → ○○."""
    text = _CITE.sub("", text)
    text = re.sub(r"\d{1,2}:\d{2}", "HH:MM", text)
    text = re.sub(r"(?<![A-Za-z\d])\d+(?:[.,]\d+)?", "N", text)
    for name in sorted(set(names) | STYLE_NAMES, key=len, reverse=True):
        text = text.replace(name, "○○")
    return text


def examples_of(item_id: str, names: set[str] = frozenset()) -> list[dict[str, str]]:
    """같은 항목의 네 단계 예시(값을 가림) — [{situation, status, judgment?, proposals}]."""
    out = []
    for ex in STYLE.get(item_id, []):
        steps = ex.get("masked") or ex.get("raw") or {}
        out.append({k: mask_example(v, names) for k, v in steps.items() if v})
    return out


def render_example(ex: dict[str, str]) -> str:
    lines = [f"{k}: {ex[k]}" for k in FACT_STEPS if ex.get(k)]
    if not ex.get("judgment"):
        lines.append('judgment: ""(이 예에는 판단할 거리가 없음)')
    if ex.get("proposals"):
        lines.append(f"proposals(제안 text를 이어 붙인 것): {ex['proposals']}")
    return "\n".join(lines)


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
        "헬기 · 인력을 몇 대 · 몇 명 더 넣으라는 수치는 쓰지 않습니다(산정 기준 없음).",
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
    "E3": (
        "요양원 · 요양병원 · 장애인 시설 같은 취약시설 거주자의 대피와 이송",
        "거주자 이송은 [판단 계산 값]의 보건소장에게 맡깁니다.",
    ),
    "E4": (
        "대피소 지정과 안내, 산불이 도로로 번질 때 교통 통제 · 우회도로 지정 · 도로 이용객 대피",
        "대피소별 배정 인원과 대피 경로는 쓰지 않습니다.",
    ),
    "E5": (
        "재난문자 · 재난방송 · 마을방송으로 대피 정보 전파",
        "재난문자 문안은 쓰지 않습니다. 마을과 대피 장소의 짝(「○○리 주민은 ○○학교로」)은 씁니다.",
    ),
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


def scope_text(item_id: str) -> str:
    """항목 범위 + 지킬 것(검사에서 「5시간 · 8시간」 같은 기준값의 출처로도 쓴다)."""
    return " ".join(ITEM_SCOPE.get(item_id, ("", "")))


def messages(
    common: dict,
    judgment: dict,
    chunks: list[dict],
    names: set[str] = frozenset(),
    guide: str = "",
    situation_line: str = "",
    value_sentences: list[str] | None = None,
    calc: list[str] | None = None,
) -> list[dict[str, str]]:
    """value_sentences = 판정 값 문장(situate.py, 코드가 판정으로 씀 — 상황 · 상태 · 판단에 옮겨 쓸 수 있음).
    calc = 판단 계산 값(calc.py, 코드가 판정 값으로 계산 — 판단 · 제안에 쓸 수 있는 새 숫자)."""
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
        + (
            f"\n이번 상황(산불 전체 — S1 · S4 · S5가 아니면 상황 문단에 옮기지 않음): {situation_line}"
            if situation_line
            else ""
        ),
        "[규칙 엔진 판정]\n" + json.dumps(judged, ensure_ascii=False),
    ]
    if value_sentences:
        parts.append(
            "[판정 값 문장 — 코드가 판정 값으로 쓴 문장입니다. 상황 · 상태 · 판단에 이 값을 옮길 때 값과 표현을 바꾸지 마십시오]\n"
            + "\n".join(f"- {s}" for s in value_sentences)
        )
    if calc:
        parts.append(
            "[판단 계산 값 — 코드가 판정 값으로 계산한 것입니다. 판단 · 제안에 쓸 수 있는 새 숫자는 이것뿐입니다]\n"
            + "\n".join(f"- {c}" for c in calc)
        )
    if topics:
        parts.append(
            "[다룰 주제 — 주제마다 제안 하나]\n"
            + "\n".join(
                f"- {tp['topic']} (소제목 {tp['section'] or '없음'} · 쓸 판정 값 {', '.join(tp['use']) or '—'})"
                for tp in topics
            )
        )
    if guide:
        parts.append("[이 항목 작성 지침 — 이번 상황에 맞춘 것]\n" + guide)
    ex = examples_of(judgment["id"], names)
    if ex:
        parts.append(
            "[문단 꼴 예시] 같은 항목을 네 단계로 쓴 예입니다(값은 N · HH:MM · ○○로 가림). 문단 나눔 · 문장 구조 · 구체성만 따르고, "
            "값 · 이름 · 판단 · 조치는 반드시 이번 판정 · 판단 계산 값 · 근거 발췌에서 가져오십시오. 예시에만 있는 대상 · 판단은 쓰지 않습니다.\n"
            + "\n\n".join(render_example(e) for e in ex)
        )
    if judgment.get("data"):
        parts.append(
            "[판정 자료 — 네 문단 아래에 목록 · 표로 함께 보입니다]\n" + "\n".join(judgment["data"])
        )
    parts.append("[근거 발췌]\n" + evidence_text(chunks))
    parts.append(
        "위 판정으로 상황 · 상태 · 판단을 쓰고, 판단에서 드러난 문제를 메우는 대응 제안을 근거 발췌에서 골라 JSON으로 쓰십시오."
    )
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
            # 첫 칸은 고정값: 자유 글이 첫 칸이면 EXAONE이 첫 글자로 「고」 · 줄바꿈을 내고 그 뒤를 지어냈다
            # (회차 1 시험 「고장산 산불」 「고산불」 — 고정값 칸을 앞에 두니 6번 중 0번, 없으면 6번 중 6번)
            "order": {"type": "string", "enum": [ORDER]},
            # 상황 → 상태 → 판단을 먼저 쓰게 한다(제안이 판단을 보고 나오게, 11/17)
            "situation": {"type": "string"},
            "status": {"type": "string"},
            "judgment": {"type": "string"},
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
            },
        },
        "required": ["order", "situation", "status", "judgment", "proposals"],
    }


def retry_message(errors: list[str], proposals: list[dict[str, Any]]) -> dict[str, str]:
    shown = "\n".join(f"- 제안 {i + 1}: {p.get('text')}" for i, p in enumerate(proposals))
    return {
        "role": "user",
        "content": "검사에서 아래 문제가 나왔습니다. 문제를 고쳐 같은 JSON 형식으로 상황 · 상태 · 판단과 제안을 모두 다시 쓰십시오. "
        "판정 · 판단 계산 값에 없는 숫자와 근거 발췌에서 찾을 수 없는 조치는 빼십시오.\n"
        + "\n".join(f"- {e}" for e in errors)
        + (f"\n\n[직전 제안]\n{shown}" if shown else ""),
    }
