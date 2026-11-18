# ruff: noqa: E501
"""제안 검사(11/8 재설계): LLM 제안이 판정과 근거를 벗어나지 않았는지 본다.

검사는 「무엇을 제안할지」를 정하지 않는다. 판정 값 · 근거 · 권한 · 형식만 본다.
- 근거: evidence가 비었거나 이번에 준 청크가 아니면 실패(근거 없는 제안은 보이지 않는다, 근거 부족 규칙)
- 판정: 숫자 · 시각 · 한글 수사 · 단계 · 이름이 판정 · 상황 · 근거 발췌에 있는지
- (11/19 없앰) 권한 검사: 다시 쓰기 지시로 법 지식(대피명령은 시장 · 군수)을 넣으므로 P1에 따라 뺐다
- 11/19 조건별(재설계 표 8): 형식 · 말투 · 되풀이 = 세 조건, 근거 ID = C2 · C3, 값 · 짝 대조 = C3만
- 형식: 「~하십시오」, 할 일 부정 금지, 「등」 금지, 3문장 · 320자 이하, 같은 제안 반복 금지,
  항목별 금칙(11/7: 추가 투입 대수 · 헬기 운항 단정 · 대피소 배정 인원 · 대피 경로 · 문자 문안)
실패하면 1회 다시 쓰게 하고(generate.py), 그래도 실패한 제안만 뺀다.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agent.prompt import CATEGORIES, FACT_STEPS, STEP_NAMES

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
    r"판정 자료|근거 발췌|규칙 엔진|판단 계산 값|판정 값 문장|evidence|withheld|do_not_state|finding|facts|JSON|입력에|○○"
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
    # 「○○리 주민은 ○○학교로」 같은 마을–대피 장소 짝(11/17 사용자 결정)은 문안이 아니다 — strip_pairs가 먼저 지운다
    "E5": [(re.compile(r"[「“\"][^」”\"]{12,}[」”\"]|문안"), "재난문자 문안은 쓰지 마십시오")],
}
_PAIR = r"[가-힣]+리(?:\s?·\s?[가-힣]+리)*\s?주민은\s?[^,」”\"]+?(?:으로|로)"
PAIR_QUOTE = re.compile(rf"[「“\"]\s?{_PAIR}(?:\s?,\s?{_PAIR})*\s?[」”\"]")


def strip_pairs(text: str) -> str:
    """금칙 검사 전에 마을–대피 장소 짝 인용(「양곡리 주민은 의성국민체육센터로, …」)을 지운다."""
    return PAIR_QUOTE.sub("", text)


# 판정에 없는 산불 이름(회차 1 시험: 「고장산 산불」 「고산불」). 「산불」 바로 앞 낱말이 판정에 없으면 걸린다
FIRE_NAME = re.compile(r"(?<![가-힣])([가-힣]{1,6})(\s?)산불")
FIRE_WORD_OK = {
    "대형",
    "이번",
    "해당",
    "현재",
    "지금",
    "이",
    "그",
    "같은",
    "이웃",
    "인접",
    "주변",
    "중소형",
}


PLACE_END = ("산", "면", "리", "군", "시", "구", "읍", "동", "봉", "령", "재")


def fire_name_errors(text: str, blob: str) -> list[str]:
    """띄어 쓴 앞말은 지명 꼴(「고장산 산불」)일 때만, 붙여 쓴 앞말(「고산불」)은 판정에 그 말이 없을 때 걸린다
    (「바람이 세져 산불 확산」 같은 서술은 걸지 않는다)."""
    out = []
    for m in FIRE_NAME.finditer(text):
        word, spaced = m.group(1), bool(m.group(2))
        if word in FIRE_WORD_OK:
            continue
        if spaced and (not word.endswith(PLACE_END) or word in blob):
            continue
        if not spaced and f"{word}산불" in blob:
            continue
        out.append(
            f"판정에 없는 산불 이름 「{m.group(0)}」 — 「산불」 또는 입력의 산불 이름만 쓰십시오"
        )
    return out


# 영어 문장(회차 1 2차: 「Information on evacuation shelters …」)과 규칙 엔진 내부 코드(「p5 구역」)
ASCII_WORDS = re.compile(r"[A-Za-z]{3,}")
ZONE_CODE = re.compile(r"(?<![A-Za-z])(?:p[0-9]|burning)(?![A-Za-z0-9])")


def language_errors(text: str) -> list[str]:
    errs = []
    if len(ASCII_WORDS.findall(text)) >= 4:
        errs.append("한국어로 쓰십시오(영어 문장)")
    if ZONE_CODE.search(text):
        errs.append(
            "「p5 · p8 · burning」 같은 내부 코드 대신 「5시간 범위 · 이미 불이 닿은」처럼 쓰십시오"
        )
    return errs


def now_area_errors(text: str, now_at: str, now_ha: Any) -> list[str]:
    """지금 면적을 다른 시각과 묶은 것(회차 1: 「14:50 무렵 48.5 ha로 확산」 — 48.5 ha는 12:50 면적)."""
    if not now_at or now_ha in (None, ""):
        return []
    ha = re.escape(str(now_ha).removesuffix(".0"))
    out = []
    for m in re.finditer(rf"(\d{{1,2}}:\d{{2}})([^.]{{0,14}}?)(?<![\d.]){ha}\s?ha", text):
        # 「11:24에 시작되어 현재 48.5 ha」처럼 사이에 「현재 · 지금」이 있으면 짝이 아니다
        if m.group(1).lstrip("0") != now_at.lstrip("0") and not re.search(r"현재|지금", m.group(2)):
            out.append(
                f"지금({now_at}) 면적 {now_ha} ha를 {m.group(1)}와 묶었습니다. 시각과 값은 판정의 짝대로 쓰십시오"
            )
    return out


UPPER = re.compile(r"도지사|특별시장|광역시장|특별자치|산림청장")
VAGUE = re.compile(r"(?:보호|대응|필요한|적절한|관련)\s?조치를\s?취하|철저히\s?하|만전을\s?기하")
MAX_LEN = 450
MAX_SENTENCES = 4
# S6 대책회의 제안은 「대책회의를 열어 …」 + 기관별 요청이라 길다(회차 1 3차: 5문장으로 빠짐)
MAX_SENTENCES_OF = {"S6": 6}
MAX_LEN_OF = {"S6": 700}
# 「정보가 없습니다」 군말(회차 1: S1 · E6 상태에 그 항목과 상관없는 「대피소 · 병원 정보 없음」을 늘어놓음)
NOINFO = re.compile(
    r"정보가 없|정보는 없|확인되지 않|명시되지 않|제공되지 않|자료가 없|알 수 없|파악할 수 없"
)


def is_noinfo_filler(s: str, withheld: list[str]) -> bool:
    """숫자가 없는 「정보 없음」 문장 가운데 withheld(판단하지 않은 것)의 낱말이 하나도 없는 것 — 뺀다(다시 쓰게 하지 않음)."""
    if not NOINFO.search(s) or re.search(r"\d", s):
        return False
    words = {w for f in withheld for w in re.split(r"[\s·,()]+", f.split("(")[0]) if len(w) >= 2}
    return not any(w in s for w in words)


def parse_out(text: str) -> dict[str, Any]:
    """LLM 답 → {situation, status, judgment, proposals}(11/17 네 단계). 추론 모드의 <think> … </think>와
    코드 울타리를 걷어 낸다. 상황 · 상태 · 판단이 없는 답(예전 꼴)은 빈 글로 채운다."""
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
    facts = {k: out.get(k) if isinstance(out.get(k), str) else "" for k in FACT_STEPS}
    return facts | {"proposals": [p for p in props if isinstance(p, dict)]}


def parse(text: str) -> list[dict[str, Any]]:
    """LLM 답 → proposals만."""
    return parse_out(text)["proposals"]


VILLAGE = re.compile(r"(?<![가-힣\d])([가-힣]{2}리)(?=[\s·,~)\]와과의에를은는도]|$)")
ORG = re.compile(
    r"(?<![가-힣])([가-힣]{1,4}[시군구])\s([가-힣]{2,8}?(?:과장|과|소방서장|소방서|경찰서장|경찰서|보건소장|보건소))(?=[에은는이가과와의을를도\s,·]|$)"
)
NOT_VILLAGE = {"일자리", "마무리", "잠자리", "실마리", "갈무리", "앞머리", "뒷머리", "보따리"}


def tidy(text: str) -> str:
    """표기만 고른다: LLM이 붙인 근거 괄호는 지우고(표기는 cite.py가 붙임) 끝 마침표를 채운다."""
    text = BRACKET.sub("", (text or "").strip())
    text = re.sub(r"(?<!\d)(\d{1,2})시\s?(\d{2})분", r"\1:\2", text)  # 「15시 50분」 → 「15:50」
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


# 제안 문장에 넣은 근거 설명(11-19 evalv3 파일럿: 제안 55개 중 46개가 「근거는 SM-p092-s01의 "…"입니다」 같은 문장을
# 넣었다 — 인용 품질을 부풀리고, 인용문이 E5 「문안」 금칙에 걸려 항목이 통째로 실패함). 근거는 evidence · quote 칸이 맡는다.
CHUNK_ID = re.compile(
    r"\s*(?<![A-Za-z0-9])(?:SM|LAW|DEC|REG|NOTI|RULE|ACT|ORD|MAN|GUIDE)-[A-Za-z0-9]+(?:-[A-Za-z0-9_]+)*(?:의|에\s?따라|에 따르면|에서|에)?"
)
CITE_SENT = re.compile(
    r"^(?:이\s?|위\s?)?(?:근거|출처)(?:는|로는|:|가|입니다|로)|근거\s?발췌|발췌\s?ID"
)


def drop_cite_sentences(text: str) -> tuple[str, list[str]]:
    """제안 글에서 근거 설명 문장(할 일 없이 근거만 말하는 것)을 지우고, 할 일 문장 안의 청크 ID는 지운다.
    → (고친 글, 지운 문장). 할 일 문장이 하나도 남지 않으면 그대로 둔다(검사가 다시 쓰게 함). 세 조건 공통(표기 정리)."""
    out, removed = [], []
    for s in split_sentences(text):
        if "십시오" not in s and (CITE_SENT.search(s) or CHUNK_ID.search(s)):
            removed.append(s)
            continue
        s2 = CHUNK_ID.sub(" ", s)
        s2 = re.sub(r"\s{2,}", " ", s2).strip()
        if s2 != s:
            removed.append(s)
        out.append(s2)
    if not any("십시오" in s for s in out):
        return text, []
    return " ".join(out), removed


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


def _stage_key(s: str) -> str:
    """단계는 번호(초기대응 · 1 · 2 · 3)로 비교한다: 판정 「2단계」와 제안 「확산대응 2단계」는 같은 단계."""
    return "초기대응" if "초기대응" in s else re.sub(r"\D", "", s)


def value_errors(
    text: str,
    allowed: tuple[set[str], set[str], str],
    judged_text: str,
    known_names: set[str],
) -> list[str]:
    """값 검사(제안 · 상황 · 상태 · 판단 공통): 숫자 · 시각 · 한글 수사 · 단계 · 이름이 판정 · 계산 값 · 근거에 있는지,
    예시의 가린 값 · 입력 설명을 옮기지 않았는지."""
    errs: list[str] = []
    times_ok, nums_ok, blob = allowed
    times, nums = _nums(text)
    if bad := sorted(times - times_ok):
        errs.append(f"판정 · 근거에 없는 시각 {bad}")
    if bad := sorted(nums - nums_ok):
        errs.append(f"판정 · 근거에 없는 숫자 {bad}")
    for word, cnt in KOR_COUNT.findall(text):
        if str(KOR_NUM[word]) not in nums_ok:
            errs.append(f"판정 · 근거에 없는 수 「{word} {cnt}」")
    judged_stages = {_stage_key(s) for s in STAGE.findall(judged_text)}
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
    return errs


# ── 대상 · 시각 짝(11/18 eval1118 원인 분석) ─────────────────────────────
# value_errors는 시각이 자료 어딘가에 있는지만 본다. 회차 1 S2 판단 「송전선은 14:50부터 불이 닿을 수 있으므로」와
# 제안 「… 14:50 전에 한국전력공사에 전류 차단」은 송전선 시간대가 13:50 ~ 14:50인데, 14:50이 다른 마을(도덕리 ·
# 신안리)의 시작 시각이라 통과했다. 여기서는 구절(쉼표로 나눈 조각)에 대상(마을 · 시설 · 도로 구간 · 송전선)과 시각이
# 함께 있으면 그 시각이 그 대상의 시간대인지 판정 facts로 본다.
# - 「A부터 · A 이후 · A 전에 · A 전까지」 = 시작 시각이어야 한다(불이 처음 닿을 수 있는 때. 끝 시각 「전에」는 늦음)
# - 「A ~ B」 「A부터 B까지」 = 그 대상의 (시작, 끝) 짝. 대상이 여럿인 구절은 모아 쓴 범위(가장 이른 시작 ~ 가장
#   늦은 끝)도 받는다(「용기리 · 도덕리 · 석탑리 순으로 13:50부터 17:50 사이」). A = B는 받지 않는다
# - 그 밖(「A까지 · A에 · A 무렵 · (A)」) = 시작이나 끝 가운데 하나
# - 구절 = 연결 어미 뒤 쉼표(「닿았고,」 「있으며,」)나 시각 앞 쉼표로 나눈 조각(「양곡리, 용기리」 같은 나열은 나누지 않음).
#   구절에 대상이 여럿이면 그 가운데 하나와 맞으면 통과(오탐을 줄인다). 대상 시각이 아닌 시각(기상 · 기준 시각)과
#   일몰 · 풍속 · 면적 · 「현재」 · 문자 발송 옆 시각은 보지 않는다. 이미 닿은(「지금」) 대상은 기준 시각을 시작으로 친다.
_T = re.compile(r"^\d{1,2}:\d{2}$")
_START_KEYS = ("from",)
_END_KEYS = ("until", "by", "arrival", "p8_until")
_OTHER_KEYS = (
    "alert_sent_at",
    "alert_at",
    "at",
)  # 대피 문자 발송 시각처럼 그 대상에 붙은 다른 시각
_SEGMENT = re.compile(r"[가-힣]+리(?:\s?~\s?[가-힣]+리)?\s?구간")  # 송전선 · 도로의 구간(마을 아님)
_RANGE = re.compile(
    r"(?<!\d)(\d{1,2}:\d{2})\s?(?:~|부터|에서)\s?(\d{1,2}:\d{2})(?!\d)(?:\s?(?:까지|사이))?"
)
_START_AFTER = re.compile(r"^\)?\s?(?:부터|이후|전에|전까지|이전|전(?![후가-힣]))")
_PAIR_SKIP = re.compile(
    r"일몰|일출|해가|해 질|풍속|풍향|바람|강풍|m/s|\d\s?ha|면적|현재|기준|문자|발송|보고"
)
_CLAUSE = re.compile(r"(?<=[고며서데만나])\s*[,，]\s+|[,，;]\s+(?=\d{1,2}:\d{2})")


def _min(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _hm(t: str) -> str:
    h, m = t.split(":")
    return f"{int(h)}:{m}"


@dataclass
class Target:
    """판정 facts의 대상 하나와 그 시각들(시작 · 끝 · 그 밖)."""

    kind: str  # village · facility · road · power
    label: str
    aliases: list[str]
    section: str = ""  # 도로 구간(「용기리」 「용기리~도덕리」)
    starts: set[str] = field(default_factory=set)
    ends: set[str] = field(default_factory=set)
    others: set[str] = field(default_factory=set)

    def span(self) -> str:
        s = " · ".join(sorted(self.starts)) or "?"
        e = " · ".join(sorted(self.ends)) or "?"
        return f"{s} ~ {e}"


def _short_names(name: str) -> list[str]:
    """시설 이름의 짧은 꼴: 「의료법인성제의료재단 성제요양병원」 → 「성제요양병원」."""
    out = [name]
    tail = name.split()[-1]
    if tail != name and len(tail) >= 3:
        out.append(tail)
    bare = re.sub(r"^의료법인\S*?재단\s?", "", name)
    if bare != name and len(bare) >= 3:
        out.append(bare)
    return list(dict.fromkeys(out))


def pair_table(items: list[dict[str, Any]], now_at: str = "") -> dict[str, Any]:
    """모든 항목의 판정 facts에서 대상 → 시각표를 만든다(항목끼리 같은 대상은 합친다).
    now_at = 기준 시각(HH:MM): 이미 닿은(「지금」) 대상의 시작으로 친다."""
    by_key: dict[tuple, Target] = {}
    now = _hm(now_at) if now_at and _T.match(now_at) else ""

    def times(d: dict[str, Any], keys: tuple[str, ...]) -> set[str]:
        return {_hm(d[k]) for k in keys if isinstance(d.get(k), str) and _T.match(d[k])}

    def walk(x: Any) -> None:
        if isinstance(x, list):
            for v in x:
                walk(v)
            return
        if not isinstance(x, dict):
            return
        st, en, ot = times(x, _START_KEYS), times(x, _END_KEYS), times(x, _OTHER_KEYS)
        if now and any(x.get(k) == "지금" for k in _END_KEYS):
            st.add(now)
        if st or en or ot:
            name = x.get("name") if isinstance(x.get("name"), str) else ""
            sec = ""
            if isinstance(x.get("road"), str):
                sec = x.get("section") or ""
                key = ("road", x["road"], sec)
                label, aliases = (f"{x['road']} {sec} 구간" if sec else x["road"]), [x["road"]]
            elif "송전선" in name:
                key, label, aliases = ("power", name), name, ["송전선"]
            elif name and (x.get("kind") or not name.endswith("리")):
                key, label, aliases = ("facility", name), name, _short_names(name)
            elif name or isinstance(x.get("village"), str):
                v = name or x["village"]
                key, label, aliases = ("village", v), v, [v]
            else:
                key = None
            if key:
                t = by_key.setdefault(key, Target(key[0], label, aliases, sec))
                t.starts |= st
                t.ends |= en
                t.others |= ot
        for v in x.values():
            walk(v)

    for it in items:
        walk(it.get("facts"))
    targets = list(by_key.values())
    return {
        "targets": targets,
        "times": {t for x in targets for t in x.starts | x.ends},
    }


def _targets_in(clause: str, table: dict[str, Any]) -> list[Target]:
    found: list[Target] = []
    roads = [t for t in table["targets"] if t.kind == "road" and t.aliases[0] in clause]
    for road in {t.aliases[0] for t in roads}:
        secs = [t for t in roads if t.aliases[0] == road]
        hit = [t for t in secs if t.section and t.section in clause]
        found += hit or secs
    for m in _SEGMENT.finditer(clause):  # 도로 이름 없이 「용기리~도덕리 구간」만 쓴 것
        place = re.sub(r"\s", "", m.group(0)).removesuffix("구간")
        found += [
            t
            for t in table["targets"]
            if t.kind == "road" and re.sub(r"\s", "", t.section) == place and t not in found
        ]
    if "송전선" in clause:
        found += [t for t in table["targets"] if t.kind == "power"]
    rest = _SEGMENT.sub(" ", clause)  # 「괴산리~신안리 구간」은 마을이 아니라 송전선 · 도로의 구간
    for t in table["targets"]:
        if t.kind == "facility" and any(a in rest for a in t.aliases):
            found.append(t)
    villages = [  # 판정의 마을 이름과만 맞댄다(「용기리는」 「방하리(」 모두)
        t
        for t in table["targets"]
        if t.kind == "village" and re.search(rf"(?<![가-힣]){re.escape(t.label)}", rest)
    ]
    found += villages
    names = {t.label for t in villages}
    if "도로" in clause:  # 「용기리 · 도덕리 일대의 도로」: 그 마을 구간의 도로도 대상
        found += [
            t
            for t in table["targets"]
            if t.kind == "road" and set(t.section.split("~")) & names and t not in found
        ]
    return found


def _time_ok(t: str, start: bool, ts: list[Target]) -> bool:
    for x in ts:
        if t in x.others:
            return True
        if start and (t in x.starts or (not x.starts and t in x.ends)):
            return True
        if not start and (t in x.starts or t in x.ends):
            return True
    return False


def pair_errors(text: str, table: dict[str, Any] | None) -> list[str]:
    """문장 속 대상과 시각의 짝이 판정 facts와 맞는지(맞지 않으면 문제 목록)."""
    if not table or not table.get("targets"):
        return []
    errs: list[str] = []
    for sent in split_sentences(text):
        for clause in _CLAUSE.split(sent):
            if not TIME.search(clause):
                continue
            ts = _targets_in(clause, table)
            if not ts:
                continue
            labels = " · ".join(dict.fromkeys(x.label for x in ts))
            spans = " / ".join(f"{x.label} {x.span()}" for x in ts[:3])
            used: list[tuple[int, int]] = []
            all_starts = {t for x in ts for t in x.starts}
            all_ends = {t for x in ts for t in x.ends}
            for m in _RANGE.finditer(clause):
                used.append(m.span())
                a, b = _hm(m.group(1)), _hm(m.group(2))
                near = clause[max(0, m.start() - 8) : m.end() + 16]
                if _PAIR_SKIP.search(near) or not ({a, b} & table["times"]):
                    continue
                one = any(a in x.starts and b in x.ends for x in ts)
                spread = (
                    len(ts) > 1 and a in all_starts and b in all_ends and _min(a) < _min(b)
                )  # 여러 대상을 모아 쓴 범위
                if not (one or spread):
                    errs.append(
                        f"「{labels}」의 시간대가 아닙니다({m.group(1)}~{m.group(2)}) — 판정: {spans}. 시각은 판정에서 그 대상과 함께 적힌 짝대로 쓰십시오"
                    )
            for m in TIME.finditer(clause):
                if any(s <= m.start() < e for s, e in used):
                    continue
                t = _hm(m.group(0))
                near = clause[max(0, m.start() - 8) : m.end() + 16]
                if _PAIR_SKIP.search(near) or t not in table["times"]:
                    continue
                start = bool(_START_AFTER.match(clause[m.end() :]))
                if not _time_ok(t, start, ts):
                    how = (
                        "시작 시각이 아닙니다(「부터 · 전에」에는 불이 처음 닿을 수 있는 시각)"
                        if start
                        else "시각이 아닙니다"
                    )
                    errs.append(
                        f"{m.group(0)}은 「{labels}」의 {how} — 판정: {spans}. 시각은 판정에서 그 대상과 함께 적힌 짝대로 쓰십시오"
                    )
    return list(dict.fromkeys(errs))


# ── 자료 값 언급(11/18: 다시 쓰기가 이름 · 값을 지운 시도를 고르지 않게) ──────────
_NAME_KEYS = {
    "name",
    "names",
    "orgs",
    "leaders",
    "villages",
    "village",
    "road",
    "section",
    "emd",
    "sigungu",
    "title",
    "agency",
}


def data_names(facts: Any) -> set[str]:
    """판정 facts의 이름 값(마을 · 시설 · 기관 · 기지 · 도로 …)과 시설 이름의 짧은 꼴."""
    out: set[str] = set()

    def walk(x: Any, key: str = "") -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, str) and key in _NAME_KEYS and re.search(r"[가-힣]", x) and len(x) >= 2:
            out.update(_short_names(x))

    walk(facts)
    return out


def data_values(text: str, allowed: tuple[set[str], set[str], str], names: set[str]) -> set[str]:
    """글에 쓴 자료 값: 판정 · 계산 값 · 근거에 있는 시각 · 숫자와 판정 facts의 이름(겹치는 이름은 긴 것만)."""
    times_ok, nums_ok, _ = allowed
    t, n = _nums(text)
    out = {f"시각 {x}" for x in t & times_ok} | {f"수 {x}" for x in n & nums_ok}
    hit = [w for w in names if w in text]
    out |= {w for w in hit if not any(w != o and w in o for o in hit)}
    return out


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
    pairs: dict[str, Any] | None = None,
    condition: str = "C3",
) -> list[str]:
    """제안 하나의 문제 목록(빈 목록 = 통과). judged_text = 판정 · 상황 글(이름 · 단계 출처),
    texts = 근거 청크 ID → 원문(있으면 quote가 단 근거에서 왔는지 본다). 11/17: quote는 근거 원문과만 대조한다
    (판정 자료와도 대조하면 매뉴얼에 없는 병원 이름 목록이 인용문으로 통과했다 — E6, 회차 1).

    11/19 재설계 표 8(condition): 말투 검사(「하십시오」 · 「등」 · 부정문 · 길이 · 문장 수 · 방법 빠진 말 ·
    항목 금칙) = 세 조건 공통, 근거 ID 검사 = C2 · C3(RAG 쪽), 값 대조(value_errors · 짝 · 「최소 N동」) = C3만
    (규칙층의 일부). 권한 검사(대피명령은 시장 · 군수)는 다시 쓰기 지시로 법 지식을 넣으므로 없앴다(P1)."""
    errs: list[str] = []
    text = p.get("text") or ""
    if p.get("category") not in CATEGORIES:
        errs.append(f"category는 {CATEGORIES} 중 하나여야 합니다")
    if not text.strip():
        return errs + ["제안이 비었습니다"]
    ev = [e for e in p.get("evidence") or [] if e in evidence_ids]
    if condition == "C1":
        pass
    elif not ev:
        errs.append(
            "근거 발췌 ID(evidence)가 없습니다. 근거 발췌에 적힌 조치만 쓰고 그 ID를 다십시오"
        )
    elif (
        texts is not None
        and strict_quote
        and not quote_ok(p.get("quote", ""), [texts.get(e, "") for e in ev])
    ):
        # 11/8 저녁: quote는 기본으로 기록만 한다(근거 뒷받침은 verify.py가 잰다). 제안을 빼는 조건으로 쓰면
        # 숫자 · 이름을 많이 쓴 풍부한 문단일수록 걸려 짧은 문장만 남았다(tune4 ~ 7)
        errs.append(
            "quote가 단 근거 원문에 없습니다. evidence로 단 발췌에서 조치를 적은 문장을 글자 그대로 옮기십시오"
        )
    max_len = MAX_LEN_OF.get(item_id, MAX_LEN)
    if len(text) > max_len:
        errs.append(f"너무 깁니다({len(text)}자, {max_len}자 이하)")
    if condition == "C3":
        errs += value_errors(text, allowed, judged_text, known_names)
        errs += pair_errors(text, pairs)
    if neg := NEG.search(text):
        errs.append(f"「{neg.group(0)}」처럼 할 일을 부정하지 말고 할 일을 쓰십시오")
    if reader and re.search(rf"{re.escape(reader)}(?:에게|께)", text):
        errs.append(f"읽는 사람({reader}) 자신에게 지시하지 말고 직접 「…하십시오」로 쓰십시오")
    if "십시오" not in text:
        errs.append(
            "할 일 문장이 없습니다. 제안에는 읽는 사람이 할 일을 「…하십시오」로 쓰십시오(「○○은 …하라」 · 「…해야 합니다」 · 「…에 해당합니다」로 끝내지 말고 「…하십시오」 또는 「○○에게 …하도록 지시하십시오」). 상황 · 상태 · 판단은 situation · status · judgment에 씁니다"
        )
    most = MAX_SENTENCES_OF.get(item_id, MAX_SENTENCES)
    if (n := len(re.findall(r"(?:다|오)\s*\.(?:\s|$)", text))) > most:
        errs.append(f"{most}문장 이하로 쓰십시오({n}문장)")
    if m := VAGUE.search(text):
        how = "구체적인 방법" if condition == "C1" else "근거 발췌의 구체적인 방법"
        errs.append(f"「{m.group(0)}」처럼 방법이 빠진 말 대신 {how}을 쓰십시오")
    if condition == "C3":
        mins = set(re.findall(r"최소 (\d[\d,]*)동", judged_text))
        for m in re.finditer(r"(\d[\d,]*)동", text):
            if m.group(1) in mins and text[max(0, m.start() - 3) : m.start()] != "최소 ":
                errs.append(f"주택 · 시설 동수는 「최소 {m.group(1)}동」으로 쓰십시오")
                break
    if m := ETC.search(text):
        near = text[max(0, m.start() - 12) : m.end() + 3].strip()
        errs.append(f"「{near}」의 「등」을 지우십시오(대상을 모두 쓰거나 빼십시오)")
    for pat, msg in FORBID.get(item_id, []):
        if pat.search(strip_pairs(text)):
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


def drop_repeated(props: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[int]]:
    """같은 항목 안에서 앞 제안과 거의 같은 문장(네 글자 조각 80% 이상 겹침)을 뒤 제안에서 지운다(회차 1 시험:
    S1 · S3에서 같은 할 일 문장이 두 제안에 되풀이됨). → (지운 문장 기록, 모든 문장이 겹친 제안 번호).
    모든 문장이 겹치면 글은 그대로 두고 번호만 돌려준다(check가 「앞 제안과 같은 내용」으로 다시 쓰게 함)."""
    seen: list[str] = []
    removed: list[dict[str, Any]] = []
    whole: set[int] = set()
    for i, p in enumerate(props):
        sents = split_sentences(p.get("text") or "")
        keep = [s for s in sents if not any(overlap(s, t) >= 0.8 for t in seen)]
        if len(keep) < len(sents):
            if any("십시오" in s for s in keep):
                removed += [{"i": i, "text": s} for s in sents if s not in keep]
                p["text"] = " ".join(keep)
            else:
                whole.add(i)
        seen += sents
    return removed, whole


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


# ── 상황 · 상태 · 판단(11/17 네 단계) ─────────────────────────────────────
FACT_MAX = {"situation": 700, "status": 800, "judgment": 450}
FACT_FORBID_IDS = ("S3", "S4")  # 사실부에도 거는 금칙: 추가 투입 대수 · 헬기 운항 단정
# 문단 앞 이름 · 기호(회차 1 시험: 「: 현재 …」 「(판단) 산불이 …」 「situation: …」)
_STEP_LABEL = re.compile(
    r"^(?:[\s:：\-–·]+|\(?(?:상황|상태|판단|situation|status|judgment)\)?\s*[:：]?\s*)+"
)
_KOR_TIME = re.compile(r"(?<!\d)(\d{1,2})시\s?(\d{2})분")  # 「14시 50분」 → 「14:50」


def tidy_fact(text: str) -> str:
    """상황 · 상태 · 판단 문단의 표기만 고른다(근거 괄호 · 문단 이름 지움, 끝 마침표)."""
    text = BRACKET.sub("", (text or "").strip())
    text = _STEP_LABEL.sub("", re.sub(r"\s+", " ", text))
    text = _KOR_TIME.sub(r"\1:\2", text)
    text = re.sub(r"\s+([.,])", r"\1", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"(\d{1,2}:\d{2})시(?![가-힣])", r"\1", text)
    text = re.sub(r"(\d{1,2}:\d{2})시([에부까])", r"\1\2", text)
    if text and text[-1] not in ".?!":
        text += "."
    return text


def fact_sentence_errors(
    item_id: str,
    s: str,
    allowed: tuple[set[str], set[str], str],
    judged_text: str,
    known_names: set[str],
    now: tuple[str, Any] | None = None,
    pairs: dict[str, Any] | None = None,
) -> list[str]:
    """상황 · 상태 · 판단의 문장 하나: 값 오류 · 할 일(「~하십시오」) · 영어 · 내부 코드 · 지어낸 산불 이름 ·
    지금 면적과 다른 시각의 짝 · 대상과 다른 시각의 짝(pairs)이 있으면 그 문장을 뺀다. now = (기준 시각 HH:MM, 지금 면적 ha)."""
    errs = value_errors(s, allowed, judged_text, known_names)
    errs += pair_errors(s, pairs)
    # 산불 이름은 사실부에만: 제안에는 「중앙산불방지대책본부」 같은 기관 이름이 섞인다
    errs += fire_name_errors(s, allowed[2])
    errs += language_errors(s)
    if now:
        errs += now_area_errors(s, *now)
    if "십시오" in s:
        errs.append("할 일(「~하십시오」)은 상황 · 상태 · 판단이 아니라 제안(proposals)에 쓰십시오")
    if item_id in FACT_FORBID_IDS:
        errs += [msg for pat, msg in FORBID.get(item_id, []) if pat.search(s)]
    return errs


def check_facts(
    item_id: str,
    facts: dict[str, Any],
    allowed: tuple[set[str], set[str], str],
    judged_text: str,
    known_names: set[str],
    now: tuple[str, Any] | None = None,
    withheld: list[str] | None = None,
    pairs: dict[str, Any] | None = None,
) -> tuple[dict[str, str], list[str], list[dict[str, Any]]]:
    """상황 · 상태 · 판단 검사 → (고친 글, 문제 목록, 뺀 문장). now = (기준 시각, 지금 면적),
    withheld = 판정의 「판단하지 않은 것」(주면 그와 상관없는 「정보 없음」 군말을 뺀다 — 문제 목록에는 안 넣음).

    값 오류(판정 · 계산 값에 없는 숫자 · 시각 · 이름)나 할 일이 든 문장은 빼고 나머지는 그대로 둔다 — 문단 전체를
    버리면 맞는 사실까지 사라지기 때문이다. 문제 목록은 다시 쓰기 메시지에 쓴다. SOFT(길이 · 「최소」 · 「등」)는
    다시 쓰게만 하고 문장을 빼지 않는다. 상황은 꼭 있어야 하고, 상태 · 판단은 쓸 거리가 없으면 비어도 된다."""
    clean: dict[str, str] = {}
    errs: list[str] = []
    removed: list[dict[str, Any]] = []
    mins = set(re.findall(r"최소 (\d[\d,]*)동", judged_text))
    for step in FACT_STEPS:
        name = STEP_NAMES[step]
        keep = []
        for s in split_sentences(tidy_fact(facts.get(step) or "")):
            if withheld is not None and is_noinfo_filler(s, withheld):
                removed.append(
                    {
                        "step": step,
                        "text": s,
                        "errors": ["정보 없음 군말(자동으로 뺌)"],
                        "filler": True,
                    }
                )
                continue
            e = fact_sentence_errors(item_id, s, allowed, judged_text, known_names, now, pairs)
            if e:
                errs.append(f"{name} 문장 「{s[:60]}」: {'; '.join(e)}")
                removed.append({"step": step, "text": s, "errors": e})
                continue
            keep.append(s)
            for m in re.finditer(r"(\d[\d,]*)동", s):
                if m.group(1) in mins and s[max(0, m.start() - 3) : m.start()] != "최소 ":
                    errs.append(
                        SOFT + f"{name}: 주택 · 시설 동수는 「최소 {m.group(1)}동」으로 쓰십시오"
                    )
                    break
            if ETC.search(s):
                errs.append(SOFT + f"{name}: 「등」을 지우고 대상을 모두 쓰거나 빼십시오")
        clean[step] = " ".join(keep)
        if len(clean[step]) > FACT_MAX[step]:
            errs.append(
                SOFT + f"{name} 문단이 너무 깁니다({len(clean[step])}자, {FACT_MAX[step]}자 이하)"
            )
    if not (facts.get("situation") or "").strip():
        errs.append("상황 문단이 비었습니다. situation에 무엇이 언제 닥치는지 쓰십시오")
    return clean, errs, removed


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
    repeated: set[int] | None = None,
    pairs: dict[str, Any] | None = None,
    condition: str = "C3",
) -> dict[int, list[str]]:
    """→ {제안 번호: 문제 목록}(문제 있는 것만). 같은 제안 반복도 잡는다(되풀이 검사 — 세 조건 공통).
    situations = {주제: 코드가 쓴 상황 문장}: 그 주제의 text(할 일 문장)는 상황 문장에 없는 숫자를 쓸 수 없다.
    repeated = drop_repeated가 찾은, 모든 문장이 앞 제안과 겹친 제안 번호.
    pairs = pair_table(항목들): 대상과 시각의 짝 검사(C3). condition = 비교 조건(check_one 참고)."""
    out: dict[int, list[str]] = {}
    seen: list[str] = []
    shapes: list[str] = []
    names = sorted(known_names, key=len, reverse=True)
    situations = situations or {}
    for i, p in enumerate(proposals):
        errs = check_one(
            item_id,
            p,
            evidence_ids,
            allowed,
            judged_text,
            known_names,
            reader,
            texts,
            strict_quote,
            pairs,
            condition,
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
        if (key and key in seen) or i in (repeated or set()):
            errs.append("앞 제안과 같은 내용입니다. 주제마다 다른 할 일을 쓰십시오")
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
