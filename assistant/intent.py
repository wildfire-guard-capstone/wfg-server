# ruff: noqa: E501
"""입력 구분: 질문(QUESTION) · 상황 정정(CORRECTION) · 그 밖(OTHER).

규칙(정규식)으로 먼저 가르고, 질문 신호와 정정 신호가 함께 있거나 둘 다 없을 때만 LLM에 묻는다
(agent-spec 6.1). LLM이 없으면 문장 끝이 물음이면 질문, 아니면 정정으로 본다.
"""

import re
from typing import Any

from assistant import prompt

QUESTION = re.compile(
    r"\?|？"
    r"|(?:^|[\s,])(?:왜|어떻게|언제|어디|어느|무엇|뭐|뭘|무슨|몇|누가|누구|얼마|어떤|어땠|어때)"
    r"|알려\s*(?:줘|주|달)|설명\s*(?:해|좀)|보여\s*(?:줘|주)|말해\s*(?:줘|주)|궁금|맞나|맞아요|되나|돼요|될까"
    r"|추천|정리해|요약해|비교해|찾아\s*(?:줘|주)"
    r"|(?:나요|까요|가요|습니까|ㅂ니까|니까|는지|은지|던가|냐|니|나|죠|지요|할까|할지|인가|일까|야\s*돼|야\s*해|야\s*하나)\s*[.!~]*\s*$"
)
CORRECTION = re.compile(
    # 마을 대피 · 명령
    r"대피\s*(?:를|가|는)?\s*(?:다\s*|모두\s*)?(?:완료|끝|마쳤|마침|했|하였|시켰|됐|되었|함|됨)"
    r"|(?:빠져\s*나|다\s*나갔|모두\s*나왔|피신\s*(?:했|완료))"
    r"|대피\s*명령\s*(?:을|이)?\s*(?:내렸|내림|발령|했|함|나갔|떨어)"
    # 자원 투입 · 철수
    r"|(?:헬기|헬리콥터|차량|소방차|진화차|지휘차|장비|인력|진화대|대원|공무원|소방관|군인|장병|경찰)\S*\s*(?:\S+\s*)?(?:\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|스물|서른)\s*(?:대|명)"
    r"|(?:추가\s*투입|더\s*(?:왔|투입|들어)|증원|철수|복귀|투입\s*(?:했|됐|되었|함|됨|완료|중))"
    # 재난문자
    r"|(?:재난\s*)?문자\s*(?:를|가|는)?\s*(?:보냈|발송|송출|나갔|했|함|됨)"
    # 정정할 수 없는 상황 변화(처리 불가 안내를 하려면 정정으로 받아야 함)
    r"|(?:[123]\s*단계|초기\s*대응|확산\s*대응|위기\s*경보|경계\s*단계|심각\s*단계)\S*\s*(?:가|이|를|로)?\s*(?:발령|격상|선포|올라|됐|되었|내려)"
    r"|(?:바람|풍속)\S*\s*(?:이|가)?\s*(?:세졌|강해|약해|바뀌|잦아|거세)"
    r"|(?:부상|다쳤|고립|갇혔|실종|연락\s*두절)"
)
# 수를 묻는 말(정정은 늘 수를 말하므로 「몇」이 있으면 질문) · 그 밖의 물음 말
ASK_COUNT = re.compile(r"몇|얼마")
ASK_WORD = re.compile(r"(?:^|[\s,])(?:왜|어떻게|언제|어디|어느|무엇|뭐|뭘|무슨|누가|누구|어떤)")
GREETING = re.compile(r"^(?:안녕|고마|감사|수고|ㅎㅇ|하이|hi|hello|테스트)", re.I)


def by_rules(text: str) -> str | None:
    """확실하면 QUESTION · CORRECTION · OTHER, 애매하면 None."""
    t = text.strip()
    if GREETING.match(t) and len(t) <= 15:
        return "OTHER"
    q, c = bool(QUESTION.search(t)), bool(CORRECTION.search(t))
    if ASK_COUNT.search(t):
        return "QUESTION"
    if q and not c:
        return "QUESTION"
    if c and not q:
        return "CORRECTION"
    return None


def classify(text: str, llm: Any | None = None, trace: dict | None = None) -> str:
    decided = by_rules(text)
    if trace is not None:
        trace["intent_rule"] = decided
    if decided is not None:
        return decided
    if llm is not None:
        raw = llm.chat(
            prompt.intent_messages(text), schema=prompt.INTENT_SCHEMA, max_tokens=40, tag="intent"
        )
        try:
            intent = prompt.loads(raw)["intent"]
        except (ValueError, KeyError, TypeError):
            intent = None
        if intent in ("QUESTION", "CORRECTION", "OTHER"):
            if trace is not None:
                trace["intent_llm"] = intent
            return intent
    # LLM 없음 · 실패: 물음표 · 물음 말 · 물음 끝말이 있으면 질문
    tail = re.search(r"(\?|？|까|나요|니|냐|어때|뭐야|는지)\s*[.!~]*\s*$", text.strip())
    if tail or ASK_WORD.search(text) or not CORRECTION.search(text):
        return "QUESTION"
    return "CORRECTION"
