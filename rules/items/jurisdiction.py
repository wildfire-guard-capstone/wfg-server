"""관할 기관 이름(11/19 평가 RE-R2): `agencies` 묶음의 관할 이름을 필요한 항목 입력에 옮긴다.

판정하지 않는다. 이름은 엔진이 산불 범위(P8)가 걸친 시군구에서 찾은 값이라 산불 위치마다 바뀌고,
자료가 없으면 null이다(「○○군 관할 소방서: 자료 없음」을 그 항목의 모르는 칸에 함께 넣는다).
전에는 이 이름이 S6 · E6 요청 표에만 있어서, S2 · E2 · E3 · E5를 쓰는 LLM이 관할 기관을 몰랐다.

「어떤 일을 어느 기관에」는 넣지 않는다 — 매뉴얼 지식이라 근거 발췌로 쓴다. 한국전력공사 ·
한국도로공사 · 재난안전상황실처럼 산불 위치와 상관없는 기관 이름도 같다.
"""

from typing import Any

MISSING = ("관할 소방서", "관할 경찰서")
GRADE = "응급의료기관 지정 등급"


def jurisdiction(bundles: dict[str, Any]) -> dict[str, Any]:
    """`{sido, by_sigungu: [{sigungu, fire, police, health}]}` — P8이 걸친 시군구마다 한 줄."""
    ag = bundles["agencies"]
    return {"sido": ag["sido"], "by_sigungu": [dict(r) for r in ag["by_sigungu"]]}


def jurisdiction_unknown(bundles: dict[str, Any]) -> list[dict[str, str]]:
    """관할 소방서 · 경찰서를 못 찾은 시군구(「청송군 관할 소방서: 자료 없음」)."""
    return [dict(u) for u in bundles["agencies"]["unknown"] if u["field"].endswith(MISSING)]


def hospitals(bundles: dict[str, Any]) -> list[dict[str, Any]]:
    """응급의료기관(산불 시군 · 인접 시군, 발화지에서 가까운 순): 이름 · 시군구 · 거리(km).

    지정 등급은 자료가 없어 빼고, 모르는 칸은 `hospitals_unknown`으로 따로 넘긴다.
    """
    return [
        {"name": h["name"], "sigungu": h["sigungu"], "km": h["km"]}
        for h in bundles["agencies"]["emergency_medical"]
    ]


def hospitals_unknown(bundles: dict[str, Any]) -> list[dict[str, str]]:
    """「응급의료기관 지정 등급: 자료 없음」(응급의료기관이 있을 때만 묶음이 넣음)."""
    return [dict(u) for u in bundles["agencies"]["unknown"] if u["field"] == GRADE]
