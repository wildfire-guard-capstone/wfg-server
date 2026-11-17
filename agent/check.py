# ruff: noqa: E501
"""제안 검사(11/8 재설계): LLM 제안이 판정과 근거를 벗어나지 않았는지 본다.

검사는 「무엇을 제안할지」를 정하지 않는다. 판정 값 · 근거 · 권한 · 형식만 본다.
- 근거: evidence가 비었거나 이번에 준 청크가 아니면 실패(근거 없는 제안은 보이지 않는다, 근거 부족 규칙)
- 판정: 숫자 · 시각 · 한글 수사 · 단계 · 이름이 판정 · 상황 · 근거 발췌에 있는지
- 권한: 읽는 사람이 시 · 도지사 이상이면 대피명령 · 위험구역 설정을 시장 · 군수에게 지시하는 꼴로
- 형식: 「~하십시오」, 할 일 부정 금지, 「등」 금지, 3문장 · 320자 이하, 같은 제안 반복 금지,
  항목별 금칙(11/7: 추가 투입 대수 · 헬기 운항 단정 · 대피소 배정 인원 · 대피 경로 · 문자 문안)
실패하면 1회 다시 쓰게 하고(generate.py), 그래도 실패한 제안만 뺀다.
"""

import json
import re
from typing import Any

from agent.prompt import CATEGORIES

TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
NUM = re.compile(r"(?<![A-Za-z\d.])\d+(?:,\d{3})*(?:\.\d+)?")
BRACKET = re.compile(r"\s*\[[^\[\]]*\]")
KOR_NUM = {
    "한": 1,
    "두": 2,
    "세": 3,
    "네": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
    "열": 10,
}
KOR_COUNT = re.compile(
    r"(?<![가-힣])(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s?(곳|명|동|대|개|가구|마을)"
)
ETC = re.compile(r"(?<=[가-힣\)\s])\s등(?=[\s,.을이의은과도에]|으로)")
STAGE = re.compile(r"초기대응|확산대응\s?[12]단계|(?<![가-힣\d])[123]단계")
NEG = re.compile(
    # 「대피하지 않은 주민」처럼 꾸미는 말(않은 · 않는 + 명사)은 할 일을 부정한 것이 아니다(tune13 E2)
    r"(?:협의|인계|대피|요청|지시|투입|배치|설정|지정|발령|통보|보고)(?:하지|시키지)\s*(?:않(?![은는]\s)|마)[가-힣]*"
)
# 문단 꼴 예시의 가린 값(N · HH:MM · ○○)을 그대로 옮긴 것
PLACEHOLDER = re.compile(
    r"HH:MM|○○|(?<![A-Za-z가-힣])N(?=\s?(?:~|m/s|ha|시|분|명|동|km|대|곳|%|개|가구))|N~N"
)
META = re.compile(
    r"판정 자료|근거 발췌|규칙 엔진|evidence|withheld|do_not_state|finding|facts|JSON|입력에|○○"
)
FORBID = {
    "S3": [
        (
            re.compile(r"추가\S*\s*\d+\s*대|\d+\s*대를?\s*(?:더|추가)|\d+\s*명을?\s*(?:더|추가)"),
            "추가 투입 대수 · 인원은 쓰지 마십시오(산정 기준 없음)",
        )
    ],
    "S4": [
        (
            re.compile(r"운항(?:이|은|을)?\s*(?:가능|불가|할 수 있|할 수 없)"),
            "헬기 운항 가능 여부를 단정하지 마십시오(운항 기준 없음)",
        )
    ],
    "E4": [
        (re.compile(r"\d[\d,]*\s*명을?\s*(?:배정|수용하)"), "대피소별 배정 인원은 쓰지 마십시오"),
        (re.compile(r"대피\s*경로|이동\s*경로|경로를 따라"), "대피 경로는 쓰지 마십시오"),
    ],
    "E5": [(re.compile(r"[「“\"][^」”\"]{12,}[」”\"]|문안"), "재난문자 문안은 쓰지 마십시오")],
}
UPPER = re.compile(r"도지사|특별시장|광역시장|특별자치|산림청장")
VAGUE = re.compile(r"(?:보호|대응|필요한|적절한|관련)\s?조치를\s?취하|철저히\s?하|만전을\s?기하")
MAX_LEN = 450
MAX_SENTENCES = 4


def parse(text: str) -> list[dict[str, Any]]:
    """LLM 답 → proposals. 추론 모드의 <think> … </think>와 코드 울타리를 걷어 낸다."""
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    if text.lstrip().startswith("["):
        s, e = text.find("["), text.rfind("]")
        out = {"proposals": json.loads(text[s : e + 1])}
    else:
        s, e = text.find("{"), text.rfind("}")
        if s < 0 or e < 0:
            raise ValueError("JSON이 없습니다")
        out = json.loads(text[s : e + 1])
    props = out.get("proposals")
    if not isinstance(props, list):
        raise ValueError("proposals가 목록이 아닙니다")
    return [p for p in props if isinstance(p, dict)]


VILLAGE = re.compile(r"(?<![가-힣\d])([가-힣]{2}리)(?=[\s·,~)\]와과의에를은는도]|$)")
ORG = re.compile(
    r"(?<![가-힣])([가-힣]{1,4}[시군구])\s([가-힣]{2,8}?(?:과장|과|소방서장|소방서|경찰서장|경찰서|보건소장|보건소))(?=[에은는이가과와의을를도\s,·]|$)"
)
NOT_VILLAGE = {"일자리", "마무리", "잠자리", "실마리", "갈무리", "앞머리", "뒷머리", "보따리"}


def tidy(text: str) -> str:
    """표기만 고른다: LLM이 붙인 근거 괄호는 지우고(표기는 cite.py가 붙임) 끝 마침표를 채운다."""
    text = BRACKET.sub("", (text or "").strip())
    text = re.sub(r"\s+([.,])", r"\1", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(
        r"(?<![가-힣])([가-힣]{2,})\s+\1(?![가-힣])", r"\1", text
    )  # 「신월리 신월리」 → 「신월리」
    text = re.sub(
        r"(\d{1,2}:\d{2})시(?![가-힣])", r"\1", text
    )  # 「15:10~16:10시에」 → 「15:10~16:10에」
    text = re.sub(r"(\d{1,2}:\d{2})시([에부까])", r"\1\2", text)
    if text and text[-1] not in ".?!":
        text += "."
    # 할 일 동사를 서술로 끝낸 것만 「~하십시오」로(tune14 E2: 「…우선 대피시킵니다.」). 「해당합니다」 같은 말은 그대로
    text = ACTION_DECL.sub(
        lambda m: (
            m.group(1) + ("시키십시오" if m.group(2) == "시킵니다" else "하십시오") + m.group(3)
        ),
        text,
    )
    return text


ACTION_DECL = re.compile(
    r"((?:대피|배치|요청|지시|투입|설정|지정|발령|통보|보고|확인|안내|전파|협의|인계|점검|준비|실시|이송|통제)\s?)(합니다|시킵니다)([.!]|\s*$)"
)


def strip_subject(text: str, reader: str) -> str:
    """읽는 사람을 주어로 쓴 것만 지운다: 「의성군수는 …하십시오」 → 「…하십시오」(문장마다, 표기만)."""
    if not reader:
        return text
    text = re.sub(rf"(^|[.!?]\s+){re.escape(reader)}님\s*,\s*", r"\1", text)  # 「의성군수님, …」
    return re.sub(rf"(^|[.!?]\s+){re.escape(reader)}(?:는|은|께서는)\s+", r"\1", text)


def fix_category(text: str, category: str) -> str:
    """구분을 문장의 할 일에 맞춘다(협의 · 요청은 문장에 그 말이 있을 때만)."""
    if "협의하십시오" in text:
        return "협의"
    if "요청하십시오" in text:
        return "요청"
    if category in ("협의", "요청"):
        return "즉시"
    return category if category in CATEGORIES else "즉시"


def _nums(text: str) -> tuple[set[str], set[str]]:
    times = {f"{int(h)}:{m}" for h, m in TIME.findall(text)}
    rest = TIME.sub(" ", text)
    nums = {n.replace(",", "") for n in NUM.findall(rest)}
    nums |= {str(int(h)) for h, _ in TIME.findall(text)}
    return times, nums


def allowed_from(*sources: Any) -> tuple[set[str], set[str], str]:
    """검사 출처 = 판정 · 상황 · 근거 발췌 글. → (시각, 숫자, 글 덩어리)."""
    blob = "\n".join(
        s if isinstance(s, str) else json.dumps(s, ensure_ascii=False) for s in sources
    )
    times, nums = _nums(blob)
    extra = {n.rstrip("0").rstrip(".") for n in nums if "." in n}
    return times, nums | extra, blob


def _norm(s: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", s or "")


QUOTE_COVER = 0.7


def quote_ok(quote: str, texts: list[str]) -> bool:
    """quote가 단 근거 원문 가운데 하나에서 온 글인가(띄어쓰기 · 문장부호 무시).

    EXAONE은 원문 문장을 옮기면서 끝말(「인계해야 한다」 → 「인계해야 합니다」)을 바꾸곤 해서 글자 그대로는 맞지 않는다.
    quote의 네 글자 조각 가운데 QUOTE_COVER(70%) 이상이 같은 근거 원문에 있으면 그 원문에서 온 것으로 본다.
    다른 청크에서 가져오거나 지어낸 문장은 이 비율이 낮다.
    """
    q = _norm(quote)
    if len(q) < 6:
        return False
    grams = {q[i : i + 4] for i in range(len(q) - 3)}
    for t in texts:
        nt = _norm(t)
        if q in nt or sum(g in nt for g in grams) / len(grams) >= QUOTE_COVER:
            return True
    return False


def check_one(
    item_id: str,
    p: dict[str, Any],
    evidence_ids: set[str],
    allowed: tuple[set[str], set[str], str],
    judged_text: str,
    known_names: set[str],
    reader: str,
    texts: dict[str, str] | None = None,
    strict_quote: bool = False,
) -> list[str]:
    """제안 하나의 문제 목록(빈 목록 = 통과). judged_text = 판정 · 상황 글(이름 · 단계 출처),
    texts = 근거 청크 ID → 원문(있으면 quote가 단 근거에서 왔는지 본다). 「_data」 = 규칙 엔진 판정 자료(목록 · 표):
    S6 · E6 요청 표처럼 규칙 엔진이 근거를 붙여 판정한 행에서 옮긴 quote도 인정한다."""
    errs: list[str] = []
    text = p.get("text") or ""
    if p.get("category") not in CATEGORIES:
        errs.append(f"category는 {CATEGORIES} 중 하나여야 합니다")
    if not text.strip():
        return errs + ["제안이 비었습니다"]
    ev = [e for e in p.get("evidence") or [] if e in evidence_ids]
    if not ev:
        errs.append(
            "근거 발췌 ID(evidence)가 없습니다. 근거 발췌에 적힌 조치만 쓰고 그 ID를 다십시오"
        )
    elif (
        texts is not None
        and strict_quote
        and not quote_ok(
            p.get("quote", ""), [texts.get(e, "") for e in ev] + [texts.get("_data", "")]
        )
    ):
        # 11/8 저녁: quote는 기본으로 기록만 한다(근거 뒷받침은 verify.py가 잰다). 제안을 빼는 조건으로 쓰면
        # 숫자 · 이름을 많이 쓴 풍부한 문단일수록 걸려 짧은 문장만 남았다(tune4 ~ 7)
        errs.append(
            "quote가 단 근거 원문에 없습니다. evidence로 단 발췌에서 조치를 적은 문장을 글자 그대로 옮기십시오"
        )
    if len(text) > MAX_LEN:
        errs.append(f"너무 깁니다({len(text)}자, {MAX_LEN}자 이하)")
    times_ok, nums_ok, blob = allowed
    times, nums = _nums(text)
    if bad := sorted(times - times_ok):
        errs.append(f"판정 · 근거에 없는 시각 {bad}")
    if bad := sorted(nums - nums_ok):
        errs.append(f"판정 · 근거에 없는 숫자 {bad}")
    for word, cnt in KOR_COUNT.findall(text):
        if str(KOR_NUM[word]) not in nums_ok:
            errs.append(f"판정 · 근거에 없는 수 「{word} {cnt}」")

    # 단계는 번호(초기대응 · 1 · 2 · 3)로 비교한다: 판정 「2단계」와 제안 「확산대응 2단계」는 같은 단계
    def _stage_key(s: str) -> str:
        return "초기대응" if "초기대응" in s else re.sub(r"\D", "", s)

    # 판정의 「초기 대응」(규칙 엔진, 띄어 씀)도 단계로 읽는다. 제안 쪽은 그대로 「초기대응」만 단계로 본다
    judged_stages = {
        _stage_key(s) for s in STAGE.findall(judged_text.replace("초기 대응", "초기대응"))
    }
    for s in STAGE.findall(text):
        if _stage_key(s) not in judged_stages:
            errs.append(f"판정에 없는 단계 「{s}」")
    for name in known_names:
        if name in text and name not in blob and re.search(rf"(?<![가-힣]){re.escape(name)}", text):
            errs.append(f"판정 · 근거에 없는 이름 「{name}」")
    # 시군 부서 · 기관 이름 오기(tune11 · 12: 「의성군 산림녹색과」 — 산림녹지과). 「○○군 ○○과(장)」 꼴만 본다
    for m in ORG.finditer(text):
        sigungu, org = m.group(1), m.group(2)
        if not any(x in blob for x in (f"{sigungu} {org}", org, org.removesuffix("장"))):
            errs.append(f"판정 · 근거에 없는 기관 이름 「{sigungu} {org}」")
    # 지도 자료에도 없는 마을 이름(tune10: 「괴산리~신신리」 — 신안리를 잘못 씀). 「○○리」 꼴만 본다
    for v in dict.fromkeys(VILLAGE.findall(text)):
        if v not in blob and v not in NOT_VILLAGE and v not in known_names:
            errs.append(f"판정 · 근거에 없는 마을 이름 「{v}」")
    if PLACEHOLDER.search(text):
        errs.append(
            "문단 꼴 예시의 가린 값(N · HH:MM · ○○)을 그대로 쓰지 말고 이번 판정의 값으로 쓰십시오"
        )
    if META.search(text):
        errs.append("입력 설명(판정 자료 · 근거 발췌 · evidence)을 문장에 옮기지 마십시오")
    if neg := NEG.search(text):
        errs.append(f"「{neg.group(0)}」처럼 할 일을 부정하지 말고 할 일을 쓰십시오")
    if reader and re.search(rf"{re.escape(reader)}(?:에게|께)", text):
        errs.append(f"읽는 사람({reader}) 자신에게 지시하지 말고 직접 「…하십시오」로 쓰십시오")
    if "십시오" not in text:
        errs.append(
            "할 일 문장이 없습니다. 상황 문장 뒤에 읽는 사람이 할 일을 「…하십시오」로 이어 쓰십시오(「○○은 …하라」 · 「…해야 합니다」 · 「…에 해당합니다」로 끝내지 말고 「…하십시오」 또는 「○○에게 …하도록 지시하십시오」)"
        )
    if (n := len(re.findall(r"(?:다|오)\s*\.(?:\s|$)", text))) > MAX_SENTENCES:
        errs.append(f"{MAX_SENTENCES}문장 이하로 쓰십시오({n}문장)")
    if m := VAGUE.search(text):
        errs.append(
            f"「{m.group(0)}」처럼 방법이 빠진 말 대신 근거 발췌의 구체적인 방법을 쓰십시오"
        )
    if UPPER.search(reader) and re.search(
        r"대피명령을\s*(?:내리|발령)|위험구역(?:으로|을)\s*설정|대피\s*권고를", text
    ):
        if not re.search(r"(?:군수|시장|구청장)에게", text):
            errs.append(
                "대피명령 · 대피 권고 · 위험구역 설정은 시장 · 군수 권한입니다. 「○○군수에게 …하도록 지시하십시오」로 쓰십시오"
            )
    mins = set(re.findall(r"최소 (\d[\d,]*)동", judged_text))
    for m in re.finditer(r"(\d[\d,]*)동", text):
        if m.group(1) in mins and text[max(0, m.start() - 3) : m.start()] != "최소 ":
            errs.append(f"주택 · 시설 동수는 「최소 {m.group(1)}동」으로 쓰십시오")
            break
    if m := ETC.search(text):
        near = text[max(0, m.start() - 12) : m.end() + 3].strip()
        errs.append(f"「{near}」의 「등」을 지우십시오(대상을 모두 쓰거나 빼십시오)")
    for pat, msg in FORBID.get(item_id, []):
        if pat.search(text):
            errs.append(msg)
    return errs


def overlap(a: str, b: str) -> float:
    """a의 네 글자 조각 가운데 b에 있는 비율(띄어쓰기 · 문장부호 무시)."""
    na, nb = _norm(a), _norm(b)
    if len(na) < 4:
        return 0.0
    grams = {na[i : i + 4] for i in range(len(na) - 3)}
    return sum(g in nb for g in grams) / len(grams)


def split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?!])\s+", (text or "").strip()) if s]


def drop_restated(text: str, situation: str) -> str:
    """상황 문장을 코드가 붙이는 주제: LLM이 상황을 다시 쓴 문장(할 일이 없고 상황 문장과 겹침)을 지운다.
    할 일 문장이 하나도 남지 않으면 그대로 둔다(검사가 「할 일 문장이 없습니다」로 다시 쓰게 함)."""
    s_times, s_nums = _nums(situation)

    def restated(s: str) -> bool:
        if "십시오" in s:
            return False
        t, n = _nums(s)
        vals = t | n
        return bool(vals) and vals <= (s_times | s_nums) or overlap(s, situation) >= 0.5

    keep = [s for s in split_sentences(text) if not restated(s)]
    return " ".join(keep) if any("십시오" in s for s in keep) else text


SOFT = "(되풀이) "  # 다시 쓰게는 하지만 제안을 빼지는 않는 문제(문체) — generate가 끝까지 남으면 그대로 쓴다
_STAGE_NUM = re.compile(r"\d단계")  # 「2단계」의 2는 값이 아니라 단계 이름


def check_action(text: str, situation: str) -> list[str]:
    """상황 문장 뒤에 붙는 할 일 문장 검사: 상황 문장에 없는 숫자 · 시각(제안을 뺌), 상황 값 되풀이(SOFT — 빼지 않음)."""
    errs = []
    times, nums = _nums(_STAGE_NUM.sub("단계", text))
    s_times, s_nums = _nums(_STAGE_NUM.sub("단계", situation))
    s_nums |= {n.rstrip("0").rstrip(".") for n in s_nums if "." in n}
    if bad := sorted((times - s_times) | (nums - s_nums)):
        errs.append(
            f"상황 문장에 없는 숫자 · 시각 {bad}을 썼습니다. 상황 문장은 코드가 앞에 붙이므로 할 일만 쓰십시오"
        )
    repeated = (times & s_times) | (nums & s_nums)
    if len(repeated) >= 2 or (len(times | nums) >= 2 and overlap(text, situation) >= 0.5):
        errs.append(
            SOFT
            + "상황 문장의 값(동수 · 면적 · 시각 · 범위)을 되풀이했습니다. 「그 주택」 「그때」처럼 가리켜 할 일만 쓰십시오"
        )
    return errs


def hard(errs: list[str]) -> list[str]:
    """제안을 빼는 문제만(SOFT 제외)."""
    return [e for e in errs if not e.startswith(SOFT)]


def check(
    item_id: str,
    proposals: list[dict[str, Any]],
    evidence_ids: set[str],
    allowed: tuple[set[str], set[str], str],
    judged_text: str,
    known_names: set[str],
    reader: str = "",
    texts: dict[str, str] | None = None,
    topics: list[str] | None = None,
    strict_quote: bool = False,
    situations: dict[str, str] | None = None,
) -> dict[int, list[str]]:
    """→ {제안 번호: 문제 목록}(문제 있는 것만). 같은 제안 반복도 잡는다.
    situations = {주제: 코드가 쓴 상황 문장}: 그 주제의 text(할 일 문장)는 상황 문장에 없는 숫자를 쓸 수 없다."""
    out: dict[int, list[str]] = {}
    seen: list[str] = []
    shapes: list[str] = []
    names = sorted(known_names, key=len, reverse=True)
    situations = situations or {}
    for i, p in enumerate(proposals):
        errs = check_one(
            item_id, p, evidence_ids, allowed, judged_text, known_names, reader, texts, strict_quote
        )
        text = p.get("text") or ""
        if (sit := situations.get(p.get("topic") or "")) is not None:
            errs += check_action(text, sit)
            # 반복 검사는 문단 전체(상황 문장 + 할 일)로: 할 일만 보면 주제가 달라도 「…협의하십시오」로 같아진다(tune12)
            text = f"{sit} {text}"
        key = re.sub(r"[^가-힣0-9]", "", text)[:30]
        # 대상(마을 · 시설 이름 · 숫자 · 시각)만 다른 같은 조치: 이름을 지운 꼴이 같으면 나눠 쓴 것
        shape = text
        for n in names:
            shape = shape.replace(n, "")
        shape = re.sub(r"[^가-힣]", "", re.sub(r"\d+", "", shape))
        if key and key in seen:
            errs.append("앞 제안과 같은 내용입니다")
        elif len(shape) >= 8 and shape in shapes:
            errs.append("같은 조치를 대상만 바꿔 나눴습니다. 대상을 한 제안에 모아 쓰십시오")
        seen.append(key)
        shapes.append(shape)
        if errs:
            out[i] = errs
    # 다룰 주제마다 제안이 있어야 한다(없으면 항목 전체를 다시 쓰게 한다)
    missing = [tp for tp in (topics or []) if not any(p.get("topic") == tp for p in proposals)]
    if missing:
        out[-1] = [
            f"다룰 주제 「{tp}」의 제안이 없습니다. 주제마다 제안을 하나씩 쓰십시오"
            for tp in missing
        ]
    return out
