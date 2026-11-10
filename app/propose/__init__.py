"""제안 작성기 — 갈아 끼우는 자리(app.predict와 같은 방식).

새 작성기는 이 폴더에 파일 하나를 만들고(base.ProposalWriter 약속을 지킴)
아래 WRITERS에 이름을 더한다.
지금: fake(테스트 문장) · agent(에이전트 — GPU 서버의 EXAONE · 근거 검색, 터널 주소로 부름).
"""

from typing import Any

from app.propose.agent import AgentWriter
from app.propose.base import LlmInput, ProposalWriter, WrittenItem
from app.propose.fake import FakeWriter

WRITERS = {
    "fake": FakeWriter,
    "agent": AgentWriter,
}
# 이 작성기는 이 설정이 꼭 있어야 한다(app.weather와 같은 방식)
REQUIRED = {"agent": ("llm", "evidence")}


def get_writer(name: str, **options: Any) -> ProposalWriter:
    """이름으로 제안 작성기를 만든다. options는 그 작성기의 설정(agent면 llm · evidence · names)."""
    if name not in WRITERS:
        raise ValueError(f"모르는 작성기: {name} (가능: {', '.join(WRITERS)})")
    for need in REQUIRED.get(name, ()):
        if options.get(need) is None:
            raise ValueError(f"작성기 {name}에는 {need} 설정이 필요합니다")
    return WRITERS[name](**options)


__all__ = ["WRITERS", "LlmInput", "ProposalWriter", "WrittenItem", "get_writer"]
