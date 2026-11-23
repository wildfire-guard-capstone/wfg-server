# ruff: noqa: E501  (지시문은 문장 단위로 둔다)
"""LLM 입력(11/19 평가 조건 재설계): 호출 1회 = 항목 하나 = 공통 지시문 + 조건별 입력 구획.

- 11/17 네 단계: 한 항목을 상황 → 상태 → 판단 → 제안 네 문단으로 쓴다(네 문단 모두 LLM).
- 11/19 재설계(「대응 제안 평가 조건 · 지표 재설계(20261118) v2」 5장 · 결정 D1 ~ D11):
  - P1 프롬프트에 매뉴얼 내용을 넣지 않는다. 조치 · 기관 · 순서 · 기준은 검색 발췌(C2 · C3)로만 들어간다.
    그래서 작성 지침(guides.py) · 문단 꼴 예시(style_examples.json) · 읽는 사람의 권한 줄을 없앴다.
  - P6 지시문(SYSTEM)은 세 조건이 글자까지 같고 「근거 규칙」 한 줄만 다르다(EVIDENCE_RULE).
    조건 차이는 사용자 입력의 구획으로만 낸다(표 7):
      공통 = 읽는 사람(직위) · 산불 기본 정보 · 없는 자료 목록 · 이번 항목 · 다루는 것 · 다른 항목 · 마지막 지시
      C1 · C2 = [원자료 표](평가 기준선 전용, demo/uiseong/raw_table.py가 만든다)
      C3 = [규칙층 판정](판정 · 판정 값 문장 · 판단 계산 값 · 판정 자료) — 제품
      C2 · C3 = [근거 발췌](맨 끝, 마지막 지시 바로 앞)
  - 구획 순서: 회차 안 12항목이 같은 앞부분(지시문 → 읽는 사람 → 산불 기본 정보 → 없는 자료 → 원자료 표)을
    공유하게 둔다(vLLM 접두 캐시). 항목마다 달라지는 구획은 그 뒤에 온다.
  - 출력: 제안 개수는 정하지 않는다(json_schema 상한 MAX_PROPOSALS — 11/19 평가 P1: 「1 ~ 5개」 제한에 48세트 중
    40세트가 꽉 차 대상이 많은 항목에서 빠뜨림). 다룰 주제 topic으로 묶던 것은 없앰(D2). evidence · quote 칸은 C2 · C3에만.
- 11/19 평가 개선(결과 덱 7쪽): P2 시한이 다른 대상을 한 시한으로 합치지 않음(규칙 9) · P3 [없는 자료]로 판단 ·
  제안을 만들지 않고 시각은 입력의 HH:MM으로(「5시간 뒤」로 바꿔 쓰지 않음, 규칙 2 · 4). 행정리: 엔진이 닿는 행정리를
  「○○리(○○1리)」로 넘기므로(main #58 RE-R3 D2) 그 꼴 그대로 쓰고 입력에 없는 행정리 이름 · 인구를 만들지 않음(규칙 1).
  C3 규칙층 구획의 지휘권자 줄에 RE-1(main #55)의 넘어가는 시간대를 함께 적음.
- 출력 형식은 vLLM json_schema로 묶는다: order(고정값) · situation · status · judgment(빈 글 가능) · proposals.
"""

import json
from typing import Any

CATEGORIES = ["즉시", "대기", "협의", "요청"]
MAX_PROPOSALS = 10  # 개수 제한이 아니라 폭주 막는 상한(지시문은 개수를 정하지 않음, 11/19 P1)
MAX_EVIDENCE = 4
CONDITIONS = ("C1", "C2", "C3")

# 근거 규칙: 세 조건 지시문에서 다른 것은 이 한 줄뿐이다(재설계 5.2)
EVIDENCE_RULE = {
    "C1": "무엇을 할지는 산불 대응에 관한 지식으로 정합니다(근거 문서는 주지 않습니다).",
    "C2": "조치와 기준은 [근거 발췌]에 적힌 것만 쓰고, 기관은 발췌나 입력 자료에 있는 것만 씁니다. 제안마다 그 발췌 ID(evidence)와 조치를 적은 원문 구절(quote)을 답니다.",
}
EVIDENCE_RULE["C3"] = EVIDENCE_RULE["C2"]
RULE_MARK = "{EVIDENCE_RULE}"

SYSTEM_TEMPLATE = """당신은 산불 통합지휘권자(읽는 사람)에게 올릴 대응 제안서의 한 항목을 쓰는 보조자입니다.
입력의 구획에 적힌 자료로 씁니다.

[쓰는 순서: 상황 → 상태 → 판단 → 제안]
한 항목을 네 문단으로 씁니다. 문단에 「상황:」 같은 이름을 붙이지 말고, 읽는 사람이 위에서 아래로 이어 읽을 수 있는 줄글로 씁니다.
- situation(상황): 불 쪽 — 무엇이 언제 닥치는가. 대상(마을 · 시설 · 도로 · 송전선 이름)과 불이 닿는 시각, 면적 · 바람의 변화를 1~3문장으로 씁니다.
- status(상태): 우리 쪽 — 지금 무엇이 있는가. 할 일을 바꾸는 조건(인원 · 시설 종류 · 대피소 거리와 수용 인원 · 투입 자원 · 지휘권자 · 관할 기관)과 정보가 없는 것을 1~4문장으로 씁니다.
- judgment(판단): 상황과 상태를 맞대 본 결과 — 무엇이 모자라거나 몰리거나 겹치거나 가장 급한가를 수치와 함께 1~3문장으로 씁니다. 판단할 거리가 없으면 빈 글("")로 둡니다.
- proposals(제안): 판단에서 드러난 문제를 메우는 할 일입니다. 아래 [제안 쓰는 법]을 따릅니다. 제안들이 이어져 네 번째 문단이 됩니다.
상황 · 상태 · 판단은 「~입니다 · ~습니다」로 끝나는 서술문으로 쓰고, 할 일(「~하십시오」)을 넣지 않습니다. 제안에는 상황 · 상태 · 판단 문장을 되풀이하지 않고 할 일에 필요한 대상 이름과 기한만 넣습니다.
- 상황은 이 항목의 대상에 대해서만 씁니다. 산불 전체의 발생 시각 · 위치 · 면적 · 바람은 S1(면적) · S4 · S5(바람)만 씁니다. 다른 항목은 그 항목의 대상(마을 · 시설 · 도로 · 송전선)에 불이 닿는 시각을 씁니다.
- 산불을 부를 때는 「산불」 또는 입력의 산불 이름만 씁니다. 입력에 없는 산 이름 · 지명을 만들지 않습니다.
- 시각과 값은 입력에서 함께 적힌 짝으로만 씁니다(틀림: 지금 면적을 다른 시각과 묶어 「HH:MM 무렵 N ha」).
- 「정보가 없습니다」는 [없는 자료]에 있는 것 가운데 이 항목의 할 일을 바꾸는 것만 한 번 씁니다. 다른 항목이 다루는 정보가 없다고 쓰지 않고, 입력에 있는 것을 없다고 쓰지 않습니다.
- 문단 앞에 「상황:」 「(판단)」 같은 이름이나 기호를 붙이지 않습니다.

[값 규칙 — 네 문단 모두]
1. 입력에 없는 값 · 이름을 지어내지 않습니다. 단계 · 시각 · 면적 · 인원 · 이름은 입력에 적힌 그대로 씁니다. 마을은 입력에 적힌 꼴 그대로 씁니다: 닿는 행정리가 괄호로 붙어 있으면(「○○리(○○1리)」) 그대로 옮기고, 입력에 없는 「○○1리」 같은 행정리 이름을 만들거나 인구를 행정리로 나누어 쓰지 않습니다(인구는 ○○리 값).
2. [없는 자료]의 값은 추측하지 않고, 그 값으로 판단 · 제안을 만들지 않습니다(틀림: 「○○ 정보가 없어 ○○에 제약이 있을 수 있습니다」 「○○ 정보가 없으므로 ○○를 줄이십시오」). 정할 수 없는 것은 「~는 정보가 없습니다」로만 밝힙니다.
3. 입력에 없는 상황(강풍 · 야간 · 피해 발생 · 통신 두절)이나 대상(도로 · 시설 · 마을)을 가정하지 않습니다. 이 항목 범위 밖의 일은 [다른 항목]이 다루므로 쓰지 않습니다.
4. 지금의 일과 앞으로의 일을 구분합니다. 「HH:MM 무렵 N단계 기준」 「지휘권자 ○○ → ○○(HH:MM ~ HH:MM)」 「HH:MM ~ HH:MM 닿을 수 있음」처럼 앞으로의 일은 「~할 수 있습니다」로 쓰고, 제안은 「…까지(…되면) …하십시오」로 씁니다(구분 대기). 아직 일어나지 않은 일을 지금 하라고 쓰지 않습니다.
   시각은 입력에 적힌 HH:MM(또는 HH:MM ~ HH:MM)으로 씁니다. 「5시간 뒤」 「N시간 후」처럼 상대 시각으로 바꿔 쓰지 않고, 같은 줄에 적힌 시각을 씁니다. 입력에 시각이 없으면 시각을 만들지 않습니다.

[제안 쓰는 법]
5. 판단에서 드러난 문제마다, 그리고 이 항목이 다루는 대상(마을 · 시설 · 도로 · 기관) 가운데 할 일이 있는 것마다 빠짐없이 제안을 씁니다. 개수는 정하지 않고 인명 보호와 급한 일부터 씁니다. 제안 하나는 1~3문장, 합쇼체입니다.
6. {EVIDENCE_RULE}
7. 구체적으로 씁니다: 누가(읽는 사람, 또는 읽는 사람이 지시 · 요청할 기관) 무엇을 어디에(마을 · 시설 · 구간) 언제까지(입력의 시각) 하는지. 일반 문구를 그대로 옮기지 말고 이번 상황의 대상 · 시각에 맞게 씁니다. 판단에서 모자람 · 몰림이 드러났으면 그것을 메우는 다음 안(두 번째 후보 · 추가 지정 · 먼저 할 대상)을 씁니다. 정보가 없어 정할 수 없는 것은 지어내지 말고 「~를 확인하십시오」처럼 확인할 일로 씁니다.
8. 할 일은 반드시 읽는 사람에게 「~하십시오」로 씁니다.
   - 읽는 사람이 직접 할 일: 「…을 발령하십시오」 「…을 ○○과 협의하십시오」
   - 소속 부서 · 하급 기관이 할 일: 「○○에게 …하도록 지시하십시오」
   - 다른 기관에 맡길 일: 「○○에 …을 요청하십시오」
   틀림: 「○○과장은 …하라」 「○○군수는 …해야 합니다」 「…조치를 취해야 합니다」(제삼자에게 시키거나 서술로 끝냄). 읽는 사람을 주어로 쓰지 않습니다(틀림: 「○○군수는 …하십시오」).
   할 일을 부정하는 문장(「~하지 않습니다」), 「등」, 「보호 조치를 취하십시오」 「철저히 하십시오」처럼 방법이 빠진 말은 쓰지 않습니다. 근거 표기([p.N] 같은 괄호) · 발췌 ID · 「근거는 …」 같은 근거 설명은 제안 문장(text)에 쓰지 않습니다.
9. 제안마다 서로 다른 조치를 씁니다. 같은 조치를 대상(마을 · 시설)만 바꿔 여러 제안으로 나누지 말고, 대상을 한 제안에 모아 씁니다(「○○리 · ○○리 주민을 …」). 다만 시한이 다른 대상을 하나의 시한으로 합치지 않습니다. 대상마다 자기 시한을 붙이거나(「○○리 · ○○리는 즉시, ○○리는 HH:MM 전까지 …」) 시한이 다른 무리끼리 제안을 나눕니다.
10. category: 즉시(지금 할 일) · 대기(조건이 되면 할 일, 미리 준비할 일) · 협의(다른 권한자와 협의) · 요청(다른 기관에 요청).

[출력] JSON 하나만: {"order": "상황 → 상태 → 판단 → 제안", "situation": "<상황 문단>", "status": "<상태 문단>", "judgment": "<판단 문단 또는 빈 글>", "proposals": [{"category": "<구분>", "text": "<제안>"}]}. 6번에 적힌 칸(evidence · quote)이 있으면 제안마다 그 칸에 따로 쓰고 text에는 넣지 않습니다."""


def system(condition: str = "C3") -> str:
    """조건별 지시문: 근거 규칙 한 줄(6번)만 다르다."""
    if condition not in CONDITIONS:
        raise ValueError(f"condition은 {CONDITIONS} 가운데 하나: {condition}")
    return SYSTEM_TEMPLATE.replace(RULE_MARK, EVIDENCE_RULE[condition])


SYSTEM = system("C3")  # 제품(C3) 지시문 — 예전 이름을 쓰는 곳을 위해 남긴다

FACT_STEPS = ("situation", "status", "judgment")
ORDER = "상황 → 상태 → 판단 → 제안"  # json_schema 첫 칸(고정값)
STEP_NAMES = {"situation": "상황", "status": "상태", "judgment": "판단", "proposals": "제안"}

# 항목 범위(보고서 틀): 이 항목이 다루는 것(검색 질의로도 쓴다)과 지킬 것. 할 일 · 매뉴얼 기준 · 담당을 넣지 않는다(재설계 표 7).
# 「지킬 것」은 항목 범위(다른 항목이 다룸)와 보고서 꼴만 둔다. 자료가 없어 정할 수 없는 것은 MISSING_DATA로 옮겼다.
ITEM_SCOPE = {
    "S1": (
        "산불 대응 단계와 통합지휘권자",
        "공식 발령 단계는 입력받지 않으므로 단계는 「판정 기준상 ○단계」로 씁니다. 지금과 앞으로를 구분해 씁니다.",
    ),
    "S2": ("진화 우선순위와 보호 대상", "주민 대피는 E2가 다룹니다."),
    "S3": ("진화자원 투입 현황과 동원", ""),
    "S4": ("시간대와 바람에 따른 진화 방식", ""),
    "S5": ("진화 인력 안전 관리", ""),
    "S6": ("유관기관 동원 · 협조와 지휘 체계 운영", ""),
    "E1": ("주민 대피를 위한 위험구역 · 잠재 위험구역 설정", ""),
    "E2": ("주민 대피 대상 · 순서 · 시점", "취약시설 거주자는 E3이, 대피소는 E4가 다룹니다."),
    "E3": ("취약시설 거주자의 대피와 이송", ""),
    "E4": (
        "대피소 지정과 안내, 산불이 닿는 도로",
        "대피소별 배정 인원과 대피 경로는 쓰지 않습니다.",
    ),
    "E5": (
        "대피 정보 전파",
        "재난문자 문안은 쓰지 않습니다. 마을과 대피 장소의 짝(「○○리 주민은 ○○학교로」)은 씁니다.",
    ),
    "E6": ("유관기관 협조 요청", ""),
}

# 없는 자료(세 조건 공통 구획, 재설계 표 7 · 표 9): 입력에 없어 정할 수 없는 것. 매뉴얼 내용이 아니라 자료의 빈칸이다.
MISSING_DATA = (
    "군사시설 위치",
    "중요 산림자원 위치",
    "헬기 운항 정보(운항 가능 여부를 정할 기준 · 기상 제한)",
    "대피소의 실제 개방 여부와 남은 수용 인원",
    "진화 인력 · 장비 · 헬기를 몇 대 · 몇 명 더 넣을지 정할 산정 기준",
    "진화 예상 시간",
    "최대순간풍속 · 시정 · 기상특보",
    "진화 인력의 현재 위치",
    "마을별 장애인 · 어린이 인원",
    "재난문자 문안(사람이 씀)",
)


def scope_of(item_id: str) -> str:
    return ITEM_SCOPE.get(item_id, ("", ""))[0]


def scope_text(item_id: str) -> str:
    """항목 범위 + 지킬 것."""
    return " ".join(x for x in ITEM_SCOPE.get(item_id, ("", "")) if x)


def evidence_text(chunks: list[dict[str, Any]]) -> str:
    parts = []
    for c in chunks:
        where = f"{c.get('doc_name', '')} {c.get('section') or ''}".strip()
        # 소제목별 검색(11/24 BLUF): 어느 소제목 질의로 찾았는지 붙인다(「검색: 시설 보호」)
        origin = f"검색: {c['part']}" if c.get("part") else c.get("origin", "")
        parts.append(f"■ {c['chunk_id']} ({c.get('label', '')} · {where} · {origin})\n{c['text']}")
    return "\n\n".join(parts)


UPPER = ("도지사", "특별시장", "광역시장", "특별자치", "산림청장")


def head_of(sigungu: str) -> str:
    """의성군 → 의성군수, ○○구 → ○○구청장, ○○시 → ○○시장."""
    if sigungu.endswith("군"):
        return sigungu + "수"
    if sigungu.endswith("구"):
        return sigungu + "청장"
    return sigungu + "장"


def basic_info(common: dict) -> str:
    """산불 기본 정보(세 조건 공통): 이름 · 위치 · 발화 시각 · 지금 시각. 단계 · 지휘권자는 판정이라 C3 구획으로."""
    inc = common.get("incident") or {}
    prop = common.get("proposal") or {}
    now = str(prop.get("base_at") or "")
    if prop.get("weekday") and now:
        now = f"{now} ({prop['weekday']})"
    rows = [
        ("이름", inc.get("name")),
        ("위치", inc.get("address")),
        ("발화 시각", inc.get("started_at")),
        ("신고 시각", inc.get("reported_at")),
        ("지금(이 제안서의 기준 시각)", now),
    ]
    return "\n".join(f"- {k}: {v}" for k, v in rows if v)


def common_parts(common: dict, raw_table: str = "", condition: str = "C3") -> list[str]:
    """회차 안 모든 항목이 같은 앞부분(접두 캐시): 읽는 사람 → 산불 기본 정보 → 없는 자료 → (C1 · C2) 원자료 표."""
    parts = [
        "[읽는 사람]\n" + str(common.get("reader", "")),
        "[산불 기본 정보]\n" + basic_info(common),
        "[없는 자료 — 입력에 없어 정할 수 없는 것]\n" + "\n".join(f"- {x}" for x in MISSING_DATA),
    ]
    if condition in ("C1", "C2"):
        if not raw_table:
            raise ValueError(f"{condition}에는 원자료 표가 있어야 합니다(raw_table)")
        parts.append(raw_table)
    return parts


RULE_HEAD = (
    "[규칙층 판정 — 규칙층이 원자료로 계산한 값입니다. 값을 바꾸지 말고, 여기 없는 계산은 하지 마십시오]\n"
    "- 판정 값은 바꾸지 않습니다. 단계 · 시각 · 면적 · 인원 · 이름은 판정에 적힌 그대로 씁니다. [판정 값 문장]이 있으면 그 값과 표현을 그대로 옮겨 써도 됩니다.\n"
    "- 새 숫자를 만들지 않습니다. 더하기 · 빼기 · 비율 같은 계산이 필요한 판단은 [판단 계산 값]에 적힌 값만 씁니다. [판단 계산 값]의 문장은 숫자의 짝(어느 대피소에 몇 명이 몰려 몇 명이 넘치는지)을 바꾸지 말고 거의 그대로 옮겨 씁니다. 거기에 없으면 계산하지 말고 판정 값끼리 맞대어 씁니다(예: 「N명 가운데 N명이 ○○리에 있습니다」).\n"
    "- withheld(판단하지 않은 것)와 do_not_state의 값은 추측하지 않습니다."
)


def rule_parts(
    common: dict,
    judgment: dict,
    situation_line: str = "",
    value_sentences: list[str] | None = None,
    calc: list[str] | None = None,
    kinds: list[str] | None = None,
    product: bool = False,
) -> list[str]:
    """C3 규칙층 구획: 판정 · 판정 값 문장 · 판단 계산 값 · 판정 자료(재설계 표 7).
    product = 11/24 소제목 형식: 「판단」 문단을 「파악」으로 부르고, 판정 자료는 소제목별 [자세히 보기 자료]로 따로 넣는다."""
    if product:
        out = rule_parts(
            common, judgment | {"data": []}, situation_line, value_sentences, calc, kinds
        )
        return [
            p.replace("상황 · 상태 · 판단에", "상황 · 상태 · 파악 · 판단 근거에").replace(
                "판단 · 제안에 쓸 수 있는", "파악 · 판단 근거 · 제안에 쓸 수 있는"
            )
            for p in out
        ]
    head = [RULE_HEAD]
    if common.get("current_stage"):
        head.append(f"지금 단계(판정 기준): {common['current_stage']}")
    cm = common.get("commander") or {}
    if cm.get("now"):
        # RE-1(main #55)의 올라가는 구간(steps: 지휘권자 · 시각 · 이유)을 함께 — 「지금 → 다음」만 주면 S1 밖 항목이
        # 「5시간 뒤 이관」으로 씀(11-19 p1p3 시범: S1만 13:50 ~ 14:50, S2 · S4 · S6은 「5시간 뒤」)
        steps = [
            f"{s.get('title', '')}({s.get('at', '')}"
            + (f", {s['why']}" if s.get("why") else "")
            + ")"
            for s in cm.get("steps") or []
            if s.get("title")
        ]
        nxt = " → ".join(steps) if steps else cm.get("next")
        show = bool(steps) or (bool(nxt) and nxt != cm["now"])
        head.append(f"지휘권자: 지금 {cm['now']}" + (f" → 다음 {nxt}" if show else ""))
    if situation_line:
        head.append(
            f"이번 상황(산불 전체 — S1 · S4 · S5가 아니면 상황 문단에 옮기지 않음): {situation_line}"
        )
    if kinds:
        head.append("판정된 대상: " + " · ".join(kinds))
    judged = {k: judgment.get(k) for k in ("finding", "facts", "withheld", "do_not_state")}
    head.append("판정: " + json.dumps(judged, ensure_ascii=False))
    parts = ["\n".join(head)]
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
    if judgment.get("data"):
        parts.append(
            "[판정 자료 — 네 문단 아래에 목록 · 표로 함께 보입니다]\n" + "\n".join(judgment["data"])
        )
    return parts


LAST = {
    "C1": "위 자료로 상황 · 상태 · 판단을 쓰고, 판단에서 드러난 문제를 메우는 대응 제안을 JSON으로 쓰십시오.",
    "C2": "위 자료로 상황 · 상태 · 판단을 쓰고, 판단에서 드러난 문제를 메우는 대응 제안을 근거 발췌에서 골라 JSON으로 쓰십시오.",
}
LAST["C3"] = LAST["C2"]


def messages(
    common: dict,
    judgment: dict,
    chunks: list[dict],
    situation_line: str = "",
    value_sentences: list[str] | None = None,
    calc: list[str] | None = None,
    condition: str = "C3",
    raw_table: str = "",
    kinds: list[str] | None = None,
) -> list[dict[str, str]]:
    """조건별 사용자 입력(재설계 표 7).
    value_sentences = 판정 값 문장(situate.py), calc = 판단 계산 값(calc.py), kinds = 판정된 대상 종류 — 모두 C3만.
    raw_table = 원자료 표 글(C1 · C2만)."""
    sys_text = system(condition)
    iid = judgment["id"]
    scope, rule = ITEM_SCOPE.get(iid, ("", ""))
    head = f"이번 항목: {iid} {judgment.get('name', '')}"
    if scope:
        head += f"\n이 항목이 다루는 것: {scope}"
    if rule:
        head += f"\n이 항목에서 지킬 것: {rule}"
    others = " · ".join(f"{k} {v[0]}" for k, v in ITEM_SCOPE.items() if k != iid)
    parts = common_parts(common, raw_table, condition) + [
        head,
        "[다른 항목 — 이 항목에 쓰지 않음]\n" + others,
    ]
    if condition == "C3":
        parts += rule_parts(common, judgment, situation_line, value_sentences, calc, kinds)
    if condition != "C1":
        parts.append("[근거 발췌]\n" + evidence_text(chunks))
    parts.append(LAST[condition])
    return [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def schema(evidence_ids: list[str], with_evidence: bool = True) -> dict[str, Any]:
    """제안 목록 json_schema: 제안 1 ~ MAX_PROPOSALS개(주제로 묶지 않음, 지시문은 개수를 정하지 않음). with_evidence = False(C1)면 evidence · quote 칸이 없다."""
    item_props: dict[str, Any] = {
        "category": {"type": "string", "enum": CATEGORIES},
        "text": {"type": "string"},
    }
    required = ["category", "text"]
    if with_evidence:
        item_props |= {
            "quote": {"type": "string"},
            "evidence": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_EVIDENCE,
                "items": {"type": "string", "enum": evidence_ids},
            },
        }
        required += ["evidence", "quote"]
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
                "minItems": 1,
                "maxItems": MAX_PROPOSALS,
                "items": {
                    "type": "object",
                    "properties": item_props,
                    "required": required,
                },
            },
        },
        "required": ["order", "situation", "status", "judgment", "proposals"],
    }


def retry_message(
    errors: list[str], proposals: list[dict[str, Any]], condition: str = "C3"
) -> dict[str, str]:
    """다시 쓰기 지시(세 조건 같은 글, C1만 근거 발췌 구절을 뺀다 — 재설계 5.4).
    11/18 eval1118: 걸린 것만 고치고 나머지는 그대로 두게 한다(회차 4 S3이 시각 하나 때문에 기지 이름 8개를 지움)."""
    shown = "\n".join(f"- 제안 {i + 1}: {p.get('text')}" for i, p in enumerate(proposals))
    tail = (
        "입력에 없는 숫자 · 시각은 그 값(과 그 값에 붙은 말)만 빼거나 입력의 값으로 바꾸십시오.\n"
        if condition == "C1"
        else "입력에 없는 숫자 · 시각은 그 값(과 그 값에 붙은 말)만 빼거나 입력의 값으로 바꾸고, 근거 발췌에서 찾을 수 없는 조치는 그 조치만 빼십시오.\n"
    )
    return {
        "role": "user",
        "content": "검사에서 아래 문제가 나왔습니다. 같은 JSON 형식으로 상황 · 상태 · 판단과 제안을 모두 다시 쓰되, 아래에 적힌 값 · 낱말 · 문장만 고치십시오. "
        "검사에 걸리지 않은 문장과 대상 이름 · 값(마을 · 시설 · 기관 · 기지 · 도로 이름, 시각, 수)은 직전 글 그대로 두고, "
        "「응원 기지」 「해당 마을」 「관련 기관」처럼 묶어 부르는 말로 바꾸거나 지우지 마십시오. "
        + tail
        + "\n".join(f"- {e}" for e in errors)
        + (f"\n\n[직전 제안]\n{shown}" if shown else ""),
    }


# ══ 11/24 소제목 형식(BLUF, 제품 C3 전용) ══════════════════════════════════════════════
# 「제안-프롬프트-개편-계획(20261124).md」 · 사용자 결정(11-24): 제품만(C1 · C2는 위 지시문 그대로), 소제목 · 자세히 보기는
# LLM의 True/False, 소제목 숨김은 코드 먼저(agent/parts.py), 판단 근거 문장의 출처는 제안과 같은 검사,
# 「판정 값: 」 「기준: 」 이름표는 LLM이 쓰고 화면에 낼 때만 지운다, 요청은 원래 항목 · S6 · E6 모두 제안 가능,
# 다른 항목은 링크가 아니라 문장 속 「(S6)」로 말한다. 생성 순서 = 상황 → 상태 → 파악 → 소제목마다
# 판단 근거 → 핵심 판단 근거 → 제안(사용자 인정). 화면 순서는 BLUF(제안 먼저).

ORDER_PARTS = "상황 → 상태 → 파악 → 소제목"  # json_schema 첫 칸(고정값)
LABEL_VALUE = "판정 값:"  # 판단 근거 첫 문장 이름표(화면에서 지움)
LABEL_RULE = "기준:"  # 판단 근거의 근거 발췌 문장 이름표(화면에서 지움)

PRODUCT_SYSTEM = """당신은 산불 통합지휘권자(읽는 사람)에게 올릴 대응 제안서의 한 항목을 쓰는 보조자입니다.
입력의 구획에 적힌 자료로 씁니다.

[쓰는 순서: 상황 → 상태 → 파악 → 소제목마다 판단 근거 → 핵심 판단 근거 → 제안]
한 항목을 현황 세 문단(상황 · 상태 · 파악)과 [소제목]에 적힌 소제목들로 씁니다. 화면에는 소제목별 제안이 맨 위에 보이고, 현황과 판단 근거는 그 아래 접힌 칸에 보입니다.
- situation(상황): 불 쪽 — 무엇이 언제 닥치는가. 대상(마을 · 시설 · 도로 · 송전선 이름)과 불이 닿는 시각, 면적 · 바람의 변화를 1~3문장으로 씁니다.
- status(상태): 우리 쪽 — 지금 무엇이 있는가. 할 일을 바꾸는 조건(인원 · 시설 종류 · 대피소 거리와 수용 인원 · 투입 자원 · 지휘권자 · 관할 기관)과 정보가 없는 것을 1~4문장으로 씁니다.
- judgment(파악): 상황과 상태를 맞대 본 결과 — 무엇이 모자라거나 몰리거나 겹치거나 가장 급한가를 수치와 함께 1~3문장으로 씁니다. 파악할 거리가 없으면 빈 글("")로 둡니다.
상황 · 상태 · 파악은 「~입니다 · ~습니다」로 끝나는 서술문으로 쓰고, 할 일(「~하십시오」)을 넣지 않습니다.
- 상황은 이 항목의 대상에 대해서만 씁니다. 산불 전체의 발생 시각 · 위치 · 면적 · 바람은 S1(면적) · S4 · S5(바람)만 씁니다. 다른 항목은 그 항목의 대상(마을 · 시설 · 도로 · 송전선)에 불이 닿는 시각을 씁니다.
- 산불을 부를 때는 「산불」 또는 입력의 산불 이름만 씁니다. 입력에 없는 산 이름 · 지명을 만들지 않습니다.
- 시각과 값은 입력에서 함께 적힌 짝으로만 씁니다(틀림: 지금 면적을 다른 시각과 묶어 「HH:MM 무렵 N ha」).
- 「정보가 없습니다」는 [없는 자료]에 있는 것 가운데 이 항목의 할 일을 바꾸는 것만 한 번 씁니다. 다른 항목이 다루는 정보가 없다고 쓰지 않고, 입력에 있는 것을 없다고 쓰지 않습니다.
- 문단 앞에 「상황:」 「(파악)」 같은 이름이나 기호를 붙이지 않습니다.

[소제목 쓰는 법]
parts에는 [소제목]에 적힌 소제목이 그 순서대로 있습니다. 소제목마다 아래 순서로 씁니다.
A. show(소제목이 둘 이상인 항목만): 이 소제목에서 읽는 사람이 할 일이 있으면 true, 없으면 false입니다. false이면 basis와 proposals는 빈 목록([]), reason은 빈 글("")로 둡니다. 적어도 한 소제목은 true입니다.
B. basis(판단 근거): 이 소제목의 제안이 나온 까닭을 2~4문장으로 쓰고, 문장 하나를 칸 하나에 넣습니다.
   - 첫 문장은 「판정 값: 」으로 시작해, 이 소제목의 제안에 쓴 판정 값(대상 이름 · 시각 · 수)을 입력에 적힌 그대로 옮깁니다. evidence는 빈 목록, quote는 빈 글입니다.
   - 다음 한두 문장은 「기준: 」으로 시작해, [근거 발췌]에 적힌 기준 · 조치를 이번 대상에 맞게 씁니다. 문장마다 그 발췌 ID(evidence)와 기준을 적은 원문 구절(quote)을 답니다.
   - 마지막 문장은 이름표 없이 판정 값과 기준을 이어 왜 이 제안을 했는지 씁니다(「그래서 …」). 정보가 없어 정하지 않은 것이 있으면 여기서 밝힙니다. evidence는 빈 목록, quote는 빈 글입니다.
   - 판단 근거 문장(text)에는 발췌 ID · 쪽 번호 · 문서 이름을 쓰지 않습니다(출처는 evidence 칸에). 원문의 머리 기호(*, -)는 옮기지 않습니다.
C. reason(핵심 판단 근거): 이 소제목의 제안을 하게 만든 가장 결정적인 판정 값(시각 · 인원 · 거리 · 수용) 한두 개를 담아 「~므로」로 끝나는 한 구절로 씁니다. 마침표를 찍지 않고 할 일을 넣지 않습니다.
D. proposals(제안): 아래 [제안 쓰는 법]을 따릅니다. 이 소제목이 다루는 일만 씁니다.
E. show_detail(입력에 [자세히 보기 자료 — 그 소제목]이 있는 소제목만): 그 자료를 화면에 펼쳐 보여야 제안을 이해할 수 있으면 true(대상 · 시각 · 기관이 여럿이라 제안 문장에 다 담기 어렵거나 수용 비교가 필요함), 제안 문장만으로 충분하면 false입니다. 자료 내용은 코드가 화면에 넣으므로 옮겨 쓰지 않습니다.

[다른 항목과 이어지는 일]
- 이 항목 범위 밖의 일(다른 항목이 다루는 대상 · 조치)은 쓰지 않습니다.
- [관련 항목의 일]은 다른 항목에서도 다루는 일입니다. 이 항목의 대상에 필요하면 이 항목에서도 제안할 수 있습니다. 이 항목의 할 일과 함께 해야 하면 그 할 일 문장 속에 「○○에 대한 ○○ 요청(S6)과 함께, …하십시오」처럼 항목 번호를 괄호로 붙여 한 번 말합니다.
- 「S6 참조」 「세부 내용은 …」 「→」처럼 다른 항목으로 넘기는 말은 쓰지 않습니다. 항목 번호는 [쓸 수 있는 다른 항목 번호]에 적힌 번호만 쓰고(진화 항목의 기관 요청은 S6, 대피 항목의 기관 요청은 E6), 현황(상황 · 상태 · 파악) 문단에는 항목 번호를 쓰지 않습니다.

[값 규칙 — 모든 칸]
1. 입력에 없는 값 · 이름을 지어내지 않습니다. 단계 · 시각 · 면적 · 인원 · 이름은 입력에 적힌 그대로 씁니다. 마을은 입력에 적힌 꼴 그대로 씁니다: 닿는 행정리가 괄호로 붙어 있으면(「○○리(○○1리)」) 그대로 옮기고, 입력에 없는 「○○1리」 같은 행정리 이름을 만들거나 인구를 행정리로 나누어 쓰지 않습니다(인구는 ○○리 값).
2. [없는 자료]의 값은 추측하지 않고, 그 값으로 파악 · 제안을 만들지 않습니다(틀림: 「○○ 정보가 없어 ○○에 제약이 있을 수 있습니다」 「○○ 정보가 없으므로 ○○를 줄이십시오」). 정할 수 없는 것은 「~는 정보가 없습니다」로만 밝힙니다.
3. 입력에 없는 상황(강풍 · 야간 · 피해 발생 · 통신 두절)이나 대상(도로 · 시설 · 마을)을 가정하지 않습니다.
4. 지금의 일과 앞으로의 일을 구분합니다. 「HH:MM 무렵 N단계 기준」 「지휘권자 ○○ → ○○(HH:MM ~ HH:MM)」 「HH:MM ~ HH:MM 닿을 수 있음」처럼 앞으로의 일은 「~할 수 있습니다」로 쓰고, 제안은 「…까지(…되면) …하십시오」로 씁니다(구분 대기). 아직 일어나지 않은 일을 지금 하라고 쓰지 않습니다.
   시각은 입력에 적힌 HH:MM(또는 HH:MM ~ HH:MM)으로 씁니다. 「5시간 뒤」 「N시간 후」처럼 상대 시각으로 바꿔 쓰지 않고, 같은 줄에 적힌 시각을 씁니다. 입력에 시각이 없으면 시각을 만들지 않습니다.

[제안 쓰는 법]
5. 파악에서 드러난 문제마다, 그리고 이 소제목이 다루는 대상(마을 · 시설 · 도로 · 기관) 가운데 할 일이 있는 것마다 빠짐없이 제안을 씁니다. 개수는 정하지 않고 인명 보호와 급한 일부터 씁니다. 제안 하나는 1~3문장, 합쇼체입니다.
6. {EVIDENCE_RULE}
7. 구체적으로 씁니다: 누가(읽는 사람, 또는 읽는 사람이 지시 · 요청할 기관) 무엇을 어디에(마을 · 시설 · 구간) 언제까지(입력의 시각) 하는지. 일반 문구를 그대로 옮기지 말고 이번 상황의 대상 · 시각에 맞게 씁니다. 파악에서 모자람 · 몰림이 드러났으면 그것을 메우는 다음 안(두 번째 후보 · 추가 지정 · 먼저 할 대상)을 씁니다. 정보가 없어 정할 수 없는 것은 지어내지 말고 「~를 확인하십시오」처럼 확인할 일로 씁니다.
8. 할 일은 반드시 읽는 사람에게 「~하십시오」로 씁니다.
   - 읽는 사람이 직접 할 일: 「…을 발령하십시오」 「…을 ○○과 협의하십시오」
   - 소속 부서 · 하급 기관이 할 일: 「○○에게 …하도록 지시하십시오」
   - 다른 기관에 맡길 일: 「○○에 …을 요청하십시오」
   틀림: 「○○과장은 …하라」 「○○군수는 …해야 합니다」 「…조치를 취해야 합니다」(제삼자에게 시키거나 서술로 끝냄). 읽는 사람을 주어로 쓰지 않습니다(틀림: 「○○군수는 …하십시오」).
   할 일을 부정하는 문장(「~하지 않습니다」), 「등」, 「보호 조치를 취하십시오」 「철저히 하십시오」처럼 방법이 빠진 말은 쓰지 않습니다. 근거 표기([p.N] 같은 괄호) · 발췌 ID · 「근거는 …」 같은 근거 설명은 제안 문장(text)에 쓰지 않습니다(근거 설명은 basis에).
9. 제안마다 서로 다른 조치를 씁니다. 같은 조치를 대상(마을 · 시설)만 바꿔 여러 제안으로 나누지 말고, 대상을 한 제안에 모아 씁니다(「○○리 · ○○리 주민을 …」). 다만 시한이 다른 대상을 하나의 시한으로 합치지 않습니다. 대상마다 자기 시한을 붙이거나(「○○리 · ○○리는 즉시, ○○리는 HH:MM 전까지 …」) 시한이 다른 무리끼리 제안을 나눕니다. 같은 조치를 두 소제목에 되풀이하지 않습니다.
10. category: 즉시(지금 할 일) · 대기(조건이 되면 할 일, 미리 준비할 일) · 협의(다른 권한자와 협의) · 요청(다른 기관에 요청).

[출력] JSON 하나만: {"order": "상황 → 상태 → 파악 → 소제목", "situation": "<상황 문단>", "status": "<상태 문단>", "judgment": "<파악 문단 또는 빈 글>", "parts": {"<소제목>": {"show": true, "basis": [{"text": "판정 값: …", "evidence": [], "quote": ""}, {"text": "기준: …", "evidence": ["<발췌 ID>"], "quote": "<원문 구절>"}, {"text": "그래서 …", "evidence": [], "quote": ""}], "reason": "…므로", "proposals": [{"category": "<구분>", "text": "<제안>", "evidence": ["<발췌 ID>"], "quote": "<원문 구절>"}], "show_detail": true}}}. parts의 소제목 · 칸은 입력에 맞춰 정해져 있습니다(show · show_detail이 없는 소제목도 있음)."""


def product_system() -> str:
    """제품(C3) 소제목 형식 지시문."""
    return PRODUCT_SYSTEM.replace(RULE_MARK, EVIDENCE_RULE["C3"])


LAST_PARTS = "위 자료로 상황 · 상태 · 파악을 쓰고, [소제목]마다 판단 근거 · 핵심 판단 근거 · 대응 제안을 근거 발췌에서 골라 JSON으로 쓰십시오."


def parts_block(parts: list[Any]) -> str:
    """[소제목] 구획. parts = agent.parts.Part 목록(title · scope · untitled)."""
    if len(parts) == 1 and parts[0].untitled:
        return f"[소제목]\n이 항목은 소제목이 없습니다. parts에 「{parts[0].title}」 하나만 씁니다."
    return "[소제목 — parts에 이 순서로 씁니다]\n" + "\n".join(
        f"- {p.title}: {p.scope}" for p in parts
    )


def detail_parts(parts: list[Any], lines_of: Any) -> list[str]:
    """소제목별 [자세히 보기 자료] 구획(자료가 있는 소제목만). lines_of = 블록 → 글줄(agent.parts.detail_lines)."""
    out = []
    for p in parts:
        if not p.detail:
            continue
        name = "이 항목" if p.untitled else p.title
        body = [ln for b in p.detail for ln in lines_of(b) if ln]
        out.append(
            f"[자세히 보기 자료 — {name}(화면에 목록 · 표로 넣을 수 있습니다. show_detail을 정할 때 봅니다)]\n"
            + "\n".join(body)
        )
    return out


def messages_parts(
    common: dict,
    judgment: dict,
    chunks: list[dict],
    parts: list[Any],
    lines_of: Any,
    related: list[str] | None = None,
    codes: list[str] | None = None,
    notes: list[str] | None = None,
    situation_line: str = "",
    value_sentences: list[str] | None = None,
    calc: list[str] | None = None,
    kinds: list[str] | None = None,
) -> list[dict[str, str]]:
    """제품(C3) 소제목 형식 사용자 입력. 앞부분(지시문 → 읽는 사람 → 산불 기본 정보 → 없는 자료)은 예전과 같아 접두 캐시를 공유한다."""
    iid = judgment["id"]
    scope, rule = ITEM_SCOPE.get(iid, ("", ""))
    head = f"이번 항목: {iid} {judgment.get('name', '')}"
    if scope:
        head += f"\n이 항목이 다루는 것: {scope}"
    if rule:
        head += f"\n이 항목에서 지킬 것: {rule}"
    others = " · ".join(f"{k} {v[0]}" for k, v in ITEM_SCOPE.items() if k != iid)
    out = common_parts(common, "", "C3") + [
        head,
        parts_block(parts),
        "[다른 항목 — 이 항목 범위 밖]\n" + others,
    ]
    if related:
        out.append(
            "[관련 항목의 일 — 다른 항목에서도 다루는 일입니다. 이 항목의 할 일과 함께 할 일이면 문장 속에 「(S6)」처럼 그 항목 번호를 붙여 말합니다]\n"
            + "\n".join(related)
        )
    # 11/24 bluf-r4: E2 · E4가 E6 대신 「(S6)」을 씀 → 쓸 수 있는 번호를 적어 준다(검사도 이 번호만 받음)
    out.append(
        "[쓸 수 있는 다른 항목 번호] "
        + (" · ".join(codes) if codes else "없음 — 이 항목에서는 다른 항목 번호를 쓰지 않습니다")
    )
    out += rule_parts(common, judgment, situation_line, value_sentences, calc, kinds, product=True)
    out += detail_parts(parts, lines_of)
    if notes:
        out.append("[규칙층 안내 — 화면 아래 「누락된 정보」에 따로 보입니다]\n" + "\n".join(notes))
    out.append("[근거 발췌]\n" + evidence_text(chunks))
    out.append(LAST_PARTS)
    return [
        {"role": "system", "content": product_system()},
        {"role": "user", "content": "\n\n".join(out)},
    ]


def parts_schema(parts: list[Any], evidence_ids: list[str]) -> dict[str, Any]:
    """소제목 형식 json_schema. parts = 이름을 키로 한 객체(순서 · 이름 고정), 소제목 칸 순서 = 생성 순서
    (show → basis → reason → proposals → show_detail). show는 소제목이 둘 이상일 때만, show_detail은 자세히 보기
    자료가 있는 소제목만 둔다."""
    ids = list(evidence_ids) or ["(없음)"]
    ev_any = {"type": "array", "maxItems": MAX_EVIDENCE, "items": {"type": "string", "enum": ids}}
    ev_one = ev_any | {"minItems": 1}
    basis_item = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "evidence": ev_any, "quote": {"type": "string"}},
        "required": ["text", "evidence", "quote"],
    }
    prop_item = {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": CATEGORIES},
            "text": {"type": "string"},
            "quote": {"type": "string"},
            "evidence": ev_one,
        },
        "required": ["category", "text", "evidence", "quote"],
    }
    multi = len(parts) > 1
    part_props: dict[str, Any] = {}
    for p in parts:
        props: dict[str, Any] = {}
        if multi:
            props["show"] = {"type": "boolean"}
        props["basis"] = {"type": "array", "maxItems": 5, "items": basis_item}
        props["reason"] = {"type": "string"}
        props["proposals"] = {
            "type": "array",
            "minItems": 0 if multi else 1,
            "maxItems": MAX_PROPOSALS,
            "items": prop_item,
        }
        if p.detail:
            props["show_detail"] = {"type": "boolean"}
        part_props[p.title] = {
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": {
            "order": {"type": "string", "enum": [ORDER_PARTS]},
            "situation": {"type": "string"},
            "status": {"type": "string"},
            "judgment": {"type": "string"},
            "parts": {
                "type": "object",
                "properties": part_props,
                "required": [p.title for p in parts],
                "additionalProperties": False,
            },
        },
        "required": ["order", "situation", "status", "judgment", "parts"],
    }


def parts_retry_message(errors: list[str], shown: list[str]) -> dict[str, str]:
    """소제목 형식 다시 쓰기 지시. errors = 「「시설 보호」 제안 2: …」처럼 소제목 이름을 붙인 문제, shown = 같은 꼴의 직전 제안."""
    return {
        "role": "user",
        "content": "검사에서 아래 문제가 나왔습니다. 같은 JSON 형식으로 상황 · 상태 · 파악과 소제목을 모두 다시 쓰되, 아래에 적힌 값 · 낱말 · 문장만 고치십시오. "
        "검사에 걸리지 않은 문장과 대상 이름 · 값(마을 · 시설 · 기관 · 기지 · 도로 이름, 시각, 수), show · show_detail은 직전 글 그대로 두고, "
        "「응원 기지」 「해당 마을」 「관련 기관」처럼 묶어 부르는 말로 바꾸거나 지우지 마십시오. "
        "입력에 없는 숫자 · 시각은 그 값(과 그 값에 붙은 말)만 빼거나 입력의 값으로 바꾸고, 근거 발췌에서 찾을 수 없는 조치 · 기준은 그것만 빼십시오.\n"
        + "\n".join(f"- {e}" for e in errors)
        + ("\n\n[직전 제안]\n" + "\n".join(shown) if shown else ""),
    }
