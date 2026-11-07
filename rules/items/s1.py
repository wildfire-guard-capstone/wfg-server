"""S1 대응단계 판단 · 격상 검토 · 지휘권 인계: 엔진 JSON 묶음에서 S1 입력을 만든다.

판정하지 않는다. `stage` · `area` · `commander` 묶음에서 고르고, 이름을 바꾸고, 틀을 채운다.
협의할지 · 인계할지는 LLM이 사실과 근거 청크(p.91 · 시행령 제21조②)를 보고 정한다(11/7).
결과는 `proposal_items.detail`에 그대로 저장되고 LLM 입력의 항목 부분이 된다.
"""

from typing import Any

from rules.core.convert import num_text

ID, NAME = "S1", "대응단계 판단·격상 검토·지휘권 인계"

# 추세 → 상황 이름(이름 바꾸기). 하향 기준이 없어 down도 유지로 봄(D, 11/5)
STATUS = {"up": "escalation_expected", "same": "maintain", "down": "maintain"}


def _decided_texts(point: dict[str, Any]) -> list[str]:
    """단계를 정한 요소 키를 글로 쓴다(틀 채우기): area → 「피해면적 48.5 ha」."""
    minimum = "최소 " if point["facility_is_minimum"] else ""
    texts = {
        "area": f"피해면적 {num_text(point['area_ha'])} ha",
        "wind": f"평균풍속 {num_text(point['wind_ms'])} m/s",
        "facility": f"시설피해 우려 {minimum}{point['facility']}동",
    }
    return [texts[key] for key in point["decided_by"]]


def _point(point: dict[str, Any]) -> dict[str, Any]:
    """한 시점을 LLM이 읽을 모양으로: 요소 키를 글로 바꾼다."""
    return {**point, "decided_by": _decided_texts(point)}


def s1_input(bundles: dict[str, Any]) -> dict[str, Any]:
    """S1 입력을 만든다.

    1. `stage`의 현재 · P5 판정을 고르고 단계를 정한 요소를 글로 바꾼다
    2. 단계 변화 · 면적 흐름 · 지휘권자를 그대로 옮긴다
    3. 상황 = 추세 이름 바꾸기
    4. finding 틀을 채운다
    5. 모르는 칸 · 판정 근거를 묶음에서 모은다
    """
    stage, area, commander = bundles["stage"], bundles["area"], bundles["commander"]
    facts = {
        "now": _point(stage["now"]),  # 1
        "p5": _point(stage["p5"]),
        "change": stage["change"],  # 2
        "area": area,
        "commander": commander,
    }
    status = STATUS[stage["trend"]]  # 3
    return {
        "id": ID,
        "name": NAME,
        "status": status,
        "finding": _finding(status, facts),  # 4
        "facts": facts,
        "targets": [],  # 산불 전체에 대한 판정이라 지도 강조 대상 없음
        "withheld": stage["unknown"] + commander["unknown"],  # 5
        "rule_basis": stage["basis"] + commander["basis"],
    }


def _basis(point: dict[str, Any]) -> str:
    """finding 괄호 안: 단계를 정한 요소, 초기 대응이면 「피해면적 ○ ha, 시설피해 우려 없음」."""
    if not point["decided_by"]:
        return f"피해면적 {num_text(point['area_ha'])} ha, 시설피해 우려 없음"
    return " · ".join(point["decided_by"])


def _finding(status: str, facts: dict[str, Any]) -> str:
    """상황마다 정한 틀에 값을 채운다.

    1. 괄호 안 근거 = 단계를 정한 요소
    2. 직전 제안서가 있으면 현재 단계 괄호에 「직전 ○」을 붙인다
    3. escalation_expected: 「현재 기준 ○(…) · P5 기준 ○(…) → 5시간 안에 ○ 기준 도달 예상」
       maintain, 두 단계가 같음: 「현재 · P5 기준 모두 ○(현재 근거 → P5 근거)」
       maintain, P5가 더 낮음: 「현재 기준 ○(…) · P5 기준 ○(…)」
    4. 끝에 「지휘권자 지금」, 바뀌면 「 → 곧」
    """
    now, p5, change = facts["now"], facts["p5"], facts["change"]
    basis_now, basis_p5 = _basis(now), _basis(p5)  # 1
    prev = "" if change["prev"] is None else f", 직전 {change['prev']}"  # 2
    s_now, s_p5 = now["stage"], p5["stage"]
    # 3
    if status == "escalation_expected":
        head = (
            f"현재 기준 {s_now}({basis_now}{prev}) · P5 기준 {s_p5}({basis_p5})"
            f" → 5시간 안에 {s_p5} 기준 도달 예상"
        )
    elif s_now == s_p5:
        head = f"현재 · P5 기준 모두 {s_now}({basis_now} → {basis_p5}{prev})"
    else:
        head = f"현재 기준 {s_now}({basis_now}{prev}) · P5 기준 {s_p5}({basis_p5})"
    # 4
    c_now, c_next = facts["commander"]["now"]["title"], facts["commander"]["next"]["title"]
    commander = c_now if c_now == c_next else f"{c_now} → {c_next}"
    return f"{head} · 지휘권자 {commander}"
