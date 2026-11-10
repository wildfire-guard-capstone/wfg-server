# ruff: noqa: E501
"""답변 문장 검사(agent-spec 6.2): 문장마다 출처가 있고, 숫자 · 시각 · 마을 이름이 그 출처에 있어야 내보낸다.

- 출처: data(D번호 = 상황 값) · docs(근거 문서 번호) 중 하나 이상, 이번에 준 번호만
- 숫자 · 시각: 문장에 나온 숫자가 단 출처(상황 값의 이름 · 값, 근거 문서 본문)에 있어야 한다.
  단 출처에 없고 다른 상황 값 · 근거 문서에 있으면 그것을 출처로 더한다(출처 바로잡기, agent.fix_evidence와 같은 생각).
  11-09 평가: EXAONE이 「평균풍속 11 m/s 이상」을 맞게 쓰고 출처만 상황 값으로 달아 떨어진 뒤, 다시 쓰면서
  「기준이 명시되어 있지 않다」고 틀리게 썼다 → 문서에 있는 숫자면 그 문서를 출처로 더해 통과시킨다.
  「2단계」 · 「P5」 · 「S3」 · 「제40조」 · 「p.75」의 숫자는 값이 아니라 이름이라 보지 않는다.
- 마을 이름(○○리): 준 자료 어디에도 없으면 지어낸 이름이다.
- 판단 금지(FORBID): 자료에 「빠진 정보」로 넘어온 것을 다른 값으로 대신 판단한 문장
  (11-09 평가: 「밤에도 헬기를 띄울 수 있습니다 … 바람 0.9 m/s로 운항에 무리가 없습니다」, 「최대 순간 풍속은 …」).
- 형식: 220자 이하, 「~다 · ~요 · ~오」로 끝남, 자료 번호 · 「상황 값」 · 「(빠진 정보)」 같은 내부 말 금지.
  문장 안의 [1] 같은 번호는 지우고, [p.75] · [재난안전법 제40조]처럼 괄호에 넣은 근거 이름은 괄호만 벗긴다.
  「~니다입니다」는 「~니다」로, 「(P5)」 같은 내부 코드 괄호는 지운다.
"""

import json
import re
from typing import Any

from agent.check import TIME, VILLAGE, _nums
from assistant.tools import Fact

NAME_NUMS = re.compile(
    r"\d\s?단계|[PpSsEe]\d{1,2}(?!\d)|\d{1,2}\s?보(?![가-힣])|제\d+조(?:의\d+)?|별표\s?\d+|p\.\s?\d+"
)
NUM_REF = re.compile(r"\s*\[\s*\d+(?:\s*[,·]\s*\d+)*\s*\]")
LABEL_REF = re.compile(r"\[([^\[\]\d][^\[\]]*)\]")
# 자료 번호 · 내부 말: 「D18」(한글이 바로 붙어도), 「[상황 값]」, 「insufficient」 …
META = re.compile(
    r"(?<![A-Za-z])D\d{1,3}(?!\d)|상황\s?값|근거\s?문서|자료\s?번호|도구\s?결과|JSON|insufficient|data_refs?|docs\b"
    r"|빠진\s?정보|MVP"  # 11-10 평가: 「진화예상시간은 (빠진 정보)로 …」 「MVP에서 제외되어 …」
)
# 11-10 평가: 「위험에 노출됩니다입니다」(끝맺음 겹침), 「5시간 뒤(P5)」(내부 코드) → 지우고 내보낸다
DOUBLE_END = re.compile(r"(니다)입니다(?=[\s.\"'」』)]|$)")
INNER_CODE = re.compile(r"\s?\(\s?[PpSsEe]\d{1,2}\s?\)")
END = re.compile(r"(?:다|요|오|까)[\"'」』)]*$")
# 「무리가 없습니다」의 「없」은 부정이 아니라 긍정 판단이다 — 부정 여부를 볼 때 먼저 지운다
NO_PROBLEM = re.compile(r"(?:무리|문제|지장|어려움|위험)(?:이|가|은|는)?\s?없\S*")
NEGATED = re.compile(
    r"없어|없습니다|없으|없는지|있는지|모릅|알\s?수\s?없|판단할\s?수\s?없|판단하지\s?않|정하지\s?않|단정|확인할\s?수\s?없|기준(?:이|은)?\s?없|정보(?:가|는)?\s?없"
    r"|명시되어\s?있지\s?않|나와\s?있지\s?않|확인되지\s?않|빠져\s?있"
)
# 「~이다 · ~한다」 → 「~입니다 · ~합니다」(11-10 평가: 한 답 안에서 말투가 섞임)
PLAIN_END = re.compile(r"(이|한|된|있|없|했|였)다(?=[\s.]*$)")
PLAIN_POLITE = {
    "이": "입니",
    "한": "합니",
    "된": "됩니",
    "있": "있습니",
    "없": "없습니",
    "했": "했습니",
    "였": "였습니",
}
FORBID: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?:헬기|헬리|공중\s?진화).{0,40}(?:띄울\s?수\s?있|띄울\s?수도|운[항용](?:이|은|을|에|도)?\s*(?:가능|할\s?수\s?있|무리가?\s?없|문제가?\s?없|지장이?\s?없)|무리가?\s?없|비행(?:이|은)?\s*가능)"
        ),
        "헬기 운항 가능 여부는 판단 기준이 없습니다. 운항할 수 있다 · 무리가 없다고 쓰지 말고 정보가 없다고 쓰십시오",
    ),
    (
        re.compile(r"순간\s?풍속"),
        "최대순간풍속은 자료에 없습니다(시간별 평균 풍속만 있음). 순간풍속이라고 쓰지 마십시오",
    ),
    (
        re.compile(
            r"(?:추가로?|더)\s?\S{0,8}\s?\d+\s?(?:대|명)(?:를|을|이|가)?\s?(?:투입|요청|동원|불러|부르)"
        ),
        "추가 투입 대수 · 인원은 산정 기준이 없어 쓰지 마십시오",
    ),
    (  # 11-10 평가: 「사전대피 지시는 … 5시간 기준 도달 예상 지역에 대한 조치이다」
        re.compile(r"사전\s?대피.{0,60}(?<![~∼\-])5\s?시간\s?(?:기준|이내|안|범위|도달)"),
        "사전대피는 잠재 위험구역(5~8시간 안에 닿는 곳) 조치입니다. 5시간 기준이라고 쓰지 마십시오",
    ),
]
NOT_VILLAGE = {
    "일자리",
    "마무리",
    "잠자리",
    "실마리",
    "갈무리",
    "앞머리",
    "뒷머리",
    "보따리",
    "거리",
    "관리",
    "처리",
}
MAX_LEN = 220


def tidy(text: str) -> str:
    t = NUM_REF.sub("", text or "")
    t = LABEL_REF.sub(r"\1", t)
    t = DOUBLE_END.sub(r"\1", t)
    t = INNER_CODE.sub("", t)
    t = PLAIN_END.sub(lambda m: PLAIN_POLITE[m.group(1)] + "다", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+([.,)])", r"\1", t)
    return t


def numbers_of(text: str) -> tuple[set[str], set[str]]:
    return _nums(NAME_NUMS.sub(" ", text))


def blob_of(facts: list[Fact], chunks: list[dict[str, Any]]) -> str:
    return "\n".join([f"{f.label} {f.value}" for f in facts] + [c.get("text", "") for c in chunks])


def allowed(blob: str) -> tuple[set[str], set[str]]:
    times, nums = _nums(blob)
    nums |= {n.rstrip("0").rstrip(".") for n in nums if "." in n}
    nums |= {n.replace(",", "") for n in re.findall(r"\d{1,3}(?:,\d{3})+", blob)}
    times |= {f"{int(h)}:{m}" for h, m in TIME.findall(blob)}
    return times, nums


def _repair(
    missing: list[str],
    candidates: list[tuple[Any, str]],
    cited: list[Any],
    ok: tuple[set[str], set[str]],
) -> list[str]:
    """빠진 숫자가 있는 후보(상황 값 · 근거 문서)를 출처에 더한다. 남은 숫자를 돌려준다."""
    ok_t, ok_n = ok
    for ref, blob in candidates:
        if not missing:
            break
        if ref in cited:
            continue
        ft, fn = allowed(blob)
        if any(m in ft or m in fn for m in missing):
            cited.append(ref)
            ok_t |= ft
            ok_n |= fn
            missing = [m for m in missing if m not in ok_t and m not in ok_n]
    return missing


def check_sentence(
    s: dict[str, Any],
    facts: dict[str, Fact],
    chunks: list[dict[str, Any]],
    all_blob: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """문장 하나 → (고친 문장 또는 None, 오류)."""
    raw = str(s.get("text", "")).strip()
    text = tidy(raw)
    errs: list[str] = []
    data = [d for d in dict.fromkeys(s.get("data") or []) if d in facts]
    docs = [
        k
        for k in dict.fromkeys(s.get("docs") or [])
        if isinstance(k, int) and 1 <= k <= len(chunks)
    ]
    if not text:
        return None, ["빈 문장"]
    head = text[:30]
    if META.search(NUM_REF.sub("", raw)):
        errs.append(f"「{head}…」: 자료 번호 · 「상황 값」 같은 내부 말을 문장에 쓰지 마십시오")
    if len(text) > MAX_LEN:
        errs.append(f"「{head}…」: 문장이 너무 깁니다({len(text)}자, {MAX_LEN}자 이하)")
    if not END.search(text.rstrip(" .")):
        errs.append(f"「{head}…」: 문장을 「~입니다 · ~습니다 · ~하십시오」로 끝내십시오")
    if not NEGATED.search(NO_PROBLEM.sub(" ", text)):
        for pat, why in FORBID:
            if pat.search(text):
                errs.append(f"「{head}…」: {why}")
    if not data and not docs:
        errs.append(f"「{head}…」: 출처(data 또는 docs)가 없습니다")
        return None, errs
    cited = blob_of([facts[d] for d in data], [chunks[k - 1] for k in docs])
    times, nums = numbers_of(text)
    ok = allowed(cited)
    missing = sorted((times - ok[0]) | (nums - ok[1] - {t.split(":")[0] for t in times}))
    if missing:  # 단 출처엔 없지만 다른 상황 값 · 근거 문서에 있으면 그것을 출처로 더한다
        missing = _repair(
            missing, [(r, f"{f.label} {f.value}") for r, f in facts.items()], data, ok
        )
        missing = _repair(
            missing, [(k, c.get("text", "")) for k, c in enumerate(chunks, 1)], docs, ok
        )
    if missing:
        errs.append(f"「{head}…」: {', '.join(missing)}은(는) 준 자료에 없는 숫자 · 시각입니다")
    for m in VILLAGE.finditer(text):
        name = m.group(1)
        if name not in NOT_VILLAGE and name not in all_blob:
            errs.append(f"「{head}…」: {name}은(는) 자료에 없는 마을입니다")
    if errs:
        return None, errs
    return {"text": text, "data": data, "docs": docs}, []


def check_answer(
    out: dict[str, Any], facts: dict[str, Fact], chunks: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """LLM 답(JSON) → (통과한 문장, 오류). 같은 문장이 두 번 나오면 한 번만."""
    all_blob = blob_of(list(facts.values()), chunks)
    good, errs, seen = [], [], set()
    for s in out.get("sentences") or []:
        fixed, e = check_sentence(s, facts, chunks, all_blob)
        errs += e
        if fixed and fixed["text"] not in seen:
            seen.add(fixed["text"])
            good.append(fixed)
    return good, errs


# ── 답하지 못한 것(insufficient) 거르기 ─────────────────────────────

PARTICLE = re.compile(
    r"(?:은|는|이|가|을|를|에|에서|에게|의|도|로|으로|와|과|랑|까지|부터|만|요|야|나요|해|해요)$"
)
GENERIC = {"여부", "가능", "기준", "정보", "자료", "수치", "구체", "상세", "현재", "지금"}


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[가-힣A-Za-z0-9]+", text or ""):
        w = PARTICLE.sub("", w)
        if len(w) >= 2 and w not in GENERIC:
            out.add(w)
    return out


def _shares(a: set[str], b: set[str]) -> bool:
    """두 낱말 묶음이 두 글자 이상 겹치는 낱말을 가졌는가(「헬기를」 · 「헬기」, 「대피는」 · 「대피」)."""
    return any(x[:2] == y[:2] or x in y or y in x for x in a for y in b)


# 질문 말투 → 자료에 쓰는 낱말(11-10 평가 5차: 「헬기를 몇 대 더 불러야 해?」의 답
# 「추가 투입 대수 … 판단할 수 없습니다」를 질문과 안 겹친다고 뺌)
ASK_HINTS: list[tuple[re.Pattern[str], set[str]]] = [
    (re.compile(r"몇\s?(?:대|명|곳|개)|얼마나|수량"), {"대수", "인원", "수량", "산정"}),
    (
        re.compile(r"더\s?(?:필요|투입|보내|동원|와야|있어야)|추가|늘려|불러|부를|부르"),
        {"추가", "투입", "동원"},
    ),
    (re.compile(r"바람|풍속|풍향"), {"바람", "풍속", "풍향"}),
    (re.compile(r"띄울|띄워|날\s?수|운항|운용|비행"), {"헬기", "운항"}),
    (re.compile(r"어디로|대피소"), {"대피소", "수용"}),
    (re.compile(r"언제까지|시한|기한"), {"시한", "기한"}),
]


def asked_words(question: str) -> set[str]:
    out = _words(question)
    for pat, extra in ASK_HINTS:
        if pat.search(question or ""):
            out |= extra
    return out


def filter_insufficient(
    items: list[str], question: str, sentences: list[dict[str, Any]], limit: int = 2
) -> list[str]:
    """질문과 상관없는 것(11-09 평가: 어느 질문에나 「추가 투입 헬기 대수」), 이미 문장으로 「없다」고 쓴 것은 뺀다."""
    q = asked_words(question)
    said = [s["text"] for s in sentences if NEGATED.search(s["text"])]
    out = []
    for x in items:
        x = x.strip().rstrip(".")
        w = _words(x)
        if not x or not w or not _shares(w, q):
            continue
        if any(_shares(w, _words(t)) for t in said):
            continue
        if x not in out:
            out.append(x)
    return out[:limit]


UNKNOWN_SAY = re.compile(
    r"(?:정보|자료)(?:가|는|에|에는|도)?\s?(?:없|빠져)|판단할\s?수\s?없|명시되어\s?있지\s?않|찾지\s?못"
)
SUBJECT_STOP = ("판단", "없", "있", "명시", "확인", "일부", "사용", "않", "대한", "관한", "같은")


def unasked_unknown(text: str, asked: str) -> bool:
    """「헬기 운항 가능 여부 · 추가 투입 대수 · 최대순간풍속 등은 정보가 없어 판단할 수 없습니다」처럼
    없다고 말하는 문장의 주어가 질문(asked)과 겹치지 않는가 — 11-10 평가 4차: 묻지도 않은 빠진 정보를
    재난문자 · 지휘권 · 도로 답 끝마다 붙임. 겹치지 않으면 그 문장은 뺀다."""
    m = UNKNOWN_SAY.search(text)
    if not m or not asked:
        return False
    subject = {w for w in _words(text[: m.start()]) if not w.startswith(SUBJECT_STOP)}
    # 주어가 없는 「자료에 없는 정보는 판단할 수 없습니다」는 아무것도 알려 주지 않는 군말이라 뺀다(6차)
    return not subject or not _shares(subject, asked_words(asked))


def dumps(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False)
