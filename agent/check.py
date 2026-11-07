"""문장 검사(11/7 결정): 숫자(한글 수사 포함) · 이름 · 근거 표기 · 금칙.

실패하면 항목 하나만 다시 쓰게 하고(1회), 그래도 실패하면 그 항목은
「문장 생성 실패」로 판정 값만 남긴다.
검사 출처(allowed) = finding · facts · common + 그 호출에 준 근거 청크 본문.
"""

import json
import re
from typing import Any

from agent.prompt import CATEGORIES, actions

TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
NUM = re.compile(r"(?<![A-Za-z\d.])\d+(?:,\d{3})*(?:\.\d+)?")
CITE = re.compile(r"\[([^\[\]]+)\]")
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
FORBID = {
    "S3": [
        (
            re.compile(r"추가\S*\s*\d+\s*대|\d+\s*대를?\s*(?:더|추가)"),
            "추가 투입 대수를 쓰면 안 됩니다(산정 기준 없음)",
        )
    ],
    "S4": [
        (
            re.compile(r"운항(?:이|은|을)?\s*(?:가능|불가|할 수 있|할 수 없)"),
            "헬기 운항 가능 여부를 단정하면 안 됩니다",
        )
    ],
    "E4": [
        (re.compile(r"\d[\d,]*\s*명을?\s*(?:배정|수용하)"), "대피소별 배정 인원을 쓰면 안 됩니다"),
        (re.compile(r"대피\s*경로|이동\s*경로|경로로|경로를 따라"), "대피 경로는 다루지 않습니다"),
    ],
    "E5": [(re.compile(r"[「“\"][^」”\"]{12,}[」”\"]|문안"), "재난문자 문안을 쓰면 안 됩니다")],
}
# 지시문 · 주의 문구를 문단에 옮겨 적은 것
META = re.compile(
    r"언급하지 않|쓰지 않습니다|다루지 않습니다|조립기|facts|finding|action|\{[a-z_]+\}|○○|HH:MM"
)


def tidy(text: str) -> str:
    """표기만 고른다(내용은 그대로): 마침표 뒤에 붙은 근거를 마침표 앞으로, 끝 마침표 채우기.

    「…입니다. [p.77, p.78]」 → 「…입니다 [p.77·78].」, 「…하십시오 [p.79]」 → 「…하십시오 [p.79].」
    """
    text = re.sub(r"\s*([.。])\s*(\[[^\[\]]+\])", r" \2\1", text.strip())
    if text.endswith("]"):
        text += "."
    return tidy_citations(text)


def parse(text: str) -> list[dict[str, Any]]:
    """LLM 답 → paragraphs. 추론 모드의 <think> … </think>와 코드 울타리를 걷어 낸다."""
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    if text.lstrip().startswith("["):  # {"paragraphs": …} 없이 목록만 낸 경우
        s, e = text.find("["), text.rfind("]")
        out = {"paragraphs": json.loads(text[s : e + 1])}
    else:
        s, e = text.find("{"), text.rfind("}")
        if s < 0 or e < 0:
            raise ValueError("JSON이 없습니다")
        out = json.loads(text[s : e + 1])
    paras = out.get("paragraphs")
    if not isinstance(paras, list):
        raise ValueError("paragraphs가 목록이 아닙니다")
    return paras


def _nums(text: str) -> tuple[set[str], set[str]]:
    times = {f"{int(h)}:{m}" for h, m in TIME.findall(text)}
    rest = TIME.sub(" ", text)
    nums = {n.replace(",", "") for n in NUM.findall(rest)}
    nums |= {str(int(h)) for h, _ in TIME.findall(text)}  # 「14 ~ 16시」의 시
    return times, nums


def allowed_from(*sources: Any) -> tuple[set[str], set[str], str]:
    blob = "\n".join(
        s if isinstance(s, str) else json.dumps(s, ensure_ascii=False) for s in sources
    )
    times, nums = _nums(blob)
    extra = set()
    for n in nums:  # 48.5 ↔ 48.50, 363.0 ↔ 363
        if "." in n:
            extra.add(n.rstrip("0").rstrip("."))
    return times, nums | extra, blob


def _expand(part: str) -> list[str]:
    """[p.72·114, 시행령 제21조①] → p.72 · p.114 · 시행령 제21조①.

    「규정 제8·9조」 → 제8조 · 제9조.
    """
    part = part.strip()
    m = re.fullmatch(r"p\.\s*(\d+(?:\s*·\s*\d+)*)", part)
    if m:
        return [f"p.{n.strip()}" for n in m.group(1).split("·")]
    m = re.fullmatch(r"(.+?제)(\d+(?:·\d+)+)(조.*)", part)
    if m:
        return [f"{m.group(1)}{n}{m.group(3)}" for n in m.group(2).split("·")]
    m = re.fullmatch(r"(.+?별표\s*)(\d+(?:·\d+)+)", part)
    if m:
        return [f"{m.group(1)}{n}" for n in m.group(2).split("·")]
    return [part]


def tidy_citations(text: str) -> str:
    """표기만 고른다(내용은 그대로): [p.73, p.91] → [p.73·91]."""

    def one(m: re.Match) -> str:
        pages, rest = [], []
        for part in m.group(1).split(","):
            for c in _expand(part):
                (pages if c.startswith("p.") else rest).append(c)
        head = ["p." + "·".join(dict.fromkeys(p[2:] for p in pages))] if pages else []
        return "[" + ", ".join(head + list(dict.fromkeys(rest))) + "]"

    return CITE.sub(one, text)


def citations(text: str) -> list[str]:
    out = []
    for m in CITE.finditer(text):
        for part in m.group(1).split(","):
            out += _expand(part)
    return out


def check_item(
    item: dict[str, Any],
    paras: list[dict[str, Any]],
    allowed: tuple[set[str], set[str], str],
    known_names: set[str],
) -> list[str]:
    errors: list[str] = []
    acts = {a["id"]: a for a in actions(item)}
    got = [p.get("action") for p in paras]
    if sorted(got) != sorted(acts):
        errors.append(f"action이 맞지 않습니다: 받은 {got}, 써야 할 {list(acts)}")
    times_ok, nums_ok, blob = allowed
    for p in paras:
        aid, text = p.get("action"), (p.get("text") or "").strip()
        a = acts.get(aid)
        if a is None:
            continue
        if p.get("category") not in CATEGORIES:
            errors.append(f"{aid}: category는 {CATEGORIES} 중 하나여야 합니다")
        if not text:
            errors.append(f"{aid}: 문단이 비었습니다")
            continue
        if len(text) > 450:
            errors.append(f"{aid}: 문단이 너무 깁니다({len(text)}자, 450자 이하)")
        body = CITE.sub(" ", text)
        times, nums = _nums(body)
        bad_t = sorted(times - times_ok)
        bad_n = sorted(nums - nums_ok)
        if bad_t:
            errors.append(f"{aid}: 입력에 없는 시각 {bad_t}")
        if bad_n:
            errors.append(f"{aid}: 입력에 없는 숫자 {bad_n}")
        for word, cnt in KOR_COUNT.findall(body):
            if str(KOR_NUM[word]) not in nums_ok:
                errors.append(f"{aid}: 입력에 없는 수 「{word} {cnt}」")
        anchors = set(a.get("anchors") or [])
        cites = citations(text)
        if cites and not anchors:
            errors.append(f"{aid}: 이 action은 근거를 달지 않습니다(anchors 없음)")
        else:
            wrong = sorted({c for c in cites if c not in anchors})
            if wrong:
                errors.append(
                    f"{aid}: anchors에 없는 근거 {wrong}(쓸 수 있는 근거 {sorted(anchors)})"
                )
        if anchors and not cites and a.get("kind") != "check":
            errors.append(f"{aid}: 할 일 문장에 근거 표기가 없습니다(예: [{sorted(anchors)[0]}])")
        if re.search(r"[.。]\s*\[[^\[\]]+\]", text):
            errors.append(f"{aid}: 근거 표기는 마침표 앞에 붙이십시오(「… 하십시오 [p.73].」)")
        see = a.get("see")
        refs = set(re.findall(r"([SE]\d)(?:-a\d+)?\s*참조", body))
        if refs - ({see} if see else set()):
            errors.append(
                f"{aid}: see가 {see or '없는'} action입니다. "
                f"「{', '.join(sorted(refs))} 참조」를 쓰지 마십시오"
            )
        for name in known_names:
            if (
                name in body
                and name not in blob
                and re.search(rf"(?<![가-힣]){re.escape(name)}", body)
            ):
                errors.append(f"{aid}: 입력에 없는 이름 「{name}」")
        if META.search(body):
            errors.append(f"{aid}: 지시문 · 주의 문구를 문단에 옮겨 적지 마십시오")
        if ETC.search(body):
            errors.append(f"{aid}: 「등」을 쓰지 말고 대상을 모두 쓰거나 빼십시오")
        for pat, msg in FORBID.get(item["id"], []):
            if pat.search(body):
                errors.append(f"{aid}: {msg}")
    return errors
