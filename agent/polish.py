# ruff: noqa: E501
"""마지막 글 다듬기(11/25): 검사를 통과한 글에서 마을 이름 꼴과 표기만 맞춘다. 할 일 · 값 · 근거는 바꾸지 않는다.

- 마을 이름: 글마다 처음 나오는 「괴산리」 「괴산1리」를 입력과 같은 「괴산리(괴산1리)」로 쓴다(프롬프트로는 LLM이 괄호를
  빼는 일이 남는다 — 시험 80~97% → 코드로 93~98%). 산불 위치(「괴산리 산 12」) · 도로 구간(「괴산리~신안리」) ·
  시설 이름과 주소(「괴산1리경로당」 「고운사(단촌면 구계리)」)는 건드리지 않는다
- 표기: 빈 괄호 「( )」, 항목 번호 「(S6)」, 깨진 「없음므로」를 고친다
"""

import re
from typing import Any

VILLAGE_ITEMS = {"S2", "S5", "E1", "E2", "E3", "E4", "E5", "E6"}  # 마을을 판정 값으로 받는 항목
_ADDRESS = re.compile(r"^\s?(?:\(?산\s?(?:일원|\d)|~|산 )")
_FACILITY = re.compile(r"^\s?(?:경로당|마을회관|회관|노인회관|분회|요양원|초등학교|저수지)")
_IN_FACILITY = re.compile(r"\([^()]*(?:요양원|병원|센터|보물|문화유산|시설)[^()]*$|요양원\($")
_IN_ADDRESS = re.compile(r"\([가-힣]+[읍면] $")  # 「고운사(단촌면 구계리)」의 앞부분
_EMPTY = re.compile(r"\s*\(\s*\)")
_CODE = re.compile(r"\s*\([SE][1-6](?:[,·\s]*[SE][1-6])*[^)]*\)")
# 「국가유산청에 (S6)와 함께 요청하십시오」: 번호만 지우면 「국가유산청에와 함께」가 된다 → 「…와 함께」도 지운다
_CODE_WITH = re.compile(r"(에게|에|을|를)\s*\([SE][1-6][^)]*\)\s*(?:과|와) 함께")
_CODE_HEAD = re.compile(r"(?<![A-Za-z0-9])[SE][1-6]\(([^)]*)\)")  # 「S6(유관기관 동원)과 함께」
_CODE_WORD = re.compile(
    r"(?<![A-Za-z0-9(·])[SE][1-6]\s(?=유관기관|재난문자|위험구역|취약시설|대피|진화)"
)
_MEURO = {
    "없음": "없으",
    "있음": "있으",
    "않음": "않으",
    "많음": "많으",
    "높음": "높으",
    "낮음": "낮으",
    "필요": "필요하",
    "초과": "초과하",
    "확대": "확대되",
    "부족": "부족하",
    "가능": "가능하",
    "예상": "예상되",
    "증가": "증가하",
    "감소": "감소하",
    "미정": "미정이",
    "미확립": "미확립이",
    "시급": "시급하",
    "전환": "전환되",
    "도달": "도달하",
    "이내": "이내이",
    "기준": "기준이",
    "예정": "예정이",
    "존재": "존재하",
    "잦아드": "잦아들",
    "있다": "있으",
    "없다": "없으",
    "넘친다": "넘치",
    "모자란다": "모자라",
    "한다": "하",
    "된다": "되",
    "하다": "하",
    "이다": "이",
}
_END = re.compile(rf"({'|'.join(_MEURO)})$")
_BROKEN = re.compile(rf"({'|'.join(_MEURO)})므로")


def village_labels(by_id: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """E1 판정의 마을 이름 → {법정리: [대피 대상 행정리]}. 행정리가 없는 이름은 뺀다."""
    zones = ((by_id.get("E1") or {}).get("facts") or {}).get("zones") or {}
    out = {}
    for r in (r for rows in zones.values() for r in rows):
        ri, _, admin = str(r.get("name", "")).partition("(")
        if admin:
            out[ri] = [x.strip() for x in admin.rstrip(")").split(",")]
    return out


def fix_names(text: str, labels: dict[str, list[str]]) -> str:
    """글에서 그 마을이 처음 나오는 곳만 「법정리(행정리)」로 고친다. 이미 그 꼴이면 그대로."""
    for ri, admin in labels.items():
        one = rf"{ri[:-1]}\d{{1,2}}리"  # 「박곡1리」 · 「박곡1리와 박곡3리」
        pat = re.compile(rf"(?<![가-힣~])(?:{ri}(\([^)]*\))?|{one}(?:\s?(?:,|·|와|및)\s?{one})*)")
        for m in pat.finditer(text):
            word, head, tail = m.group(0), text[: m.start()], text[m.end() :]
            if _FACILITY.match(tail) or _ADDRESS.match(tail) or _IN_FACILITY.search(head):
                continue
            if _IN_ADDRESS.search(head) and tail.startswith(")"):
                continue
            if m.group(1):
                if any(a in m.group(1) for a in admin):
                    break  # 이미 「법정리(행정리)」
                continue
            said = admin if word == ri else re.findall(one, word)
            if not set(said) <= set(admin):
                continue  # 대피 대상이 아닌 행정리
            text = f"{head}{ri}({', '.join(said)}){tail}"
            break
    return text


def fix_marks(text: str) -> str:
    """빈 괄호 · 항목 번호 괄호 · 깨진 「므로」를 고친다."""
    text = _CODE_WITH.sub(r"\1", _EMPTY.sub("", text))
    text = _CODE_HEAD.sub(r"\1", _CODE.sub("", text))
    text = re.sub(r"\.{2,}", ".", _CODE_WORD.sub("", text))
    return _BROKEN.sub(lambda m: _MEURO[m.group(1)] + "므로", text)


def fix_reason(text: str) -> str:
    """핵심 판단 근거가 「~므로」로 끝나지 않으면 맞춘다(「정보 없음」 → 「정보 없으므로」)."""
    return _END.sub(lambda m: _MEURO[m.group(1)] + "므로", text.rstrip(" .,"))


def polish(
    item_id: str,
    final: dict[str, dict[str, Any]],
    facts: dict[str, str],
    labels: dict[str, list[str]],
) -> None:
    """현황 · 제안 · 핵심 판단 근거 · 판단 근거의 글을 제자리에서 고친다(인용 quote는 그대로)."""
    names = labels if item_id in VILLAGE_ITEMS else {}

    def fix(text: str) -> str:
        return fix_names(fix_marks(text), names)

    for step, text in facts.items():
        facts[step] = fix(text or "")
    for part in final.values():
        part["reason"] = fix(fix_reason(part.get("reason") or ""))
        for row in [*(part.get("proposals") or []), *(part.get("basis") or [])]:
            row["text"] = fix(row.get("text") or "")
