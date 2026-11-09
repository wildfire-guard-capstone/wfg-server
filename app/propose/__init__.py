"""제안 작성기 — 갈아 끼우는 자리(app.predict와 같은 방식).

새 작성기는 이 폴더에 파일 하나를 만들고(base.ProposalWriter 약속을 지킴)
아래 WRITERS에 이름을 더한다.
지금: fake(테스트 문장). 나중: agent(팀원 에이전트 — GPU 서버의 EXAONE · 근거 검색).
"""

from app.propose.base import LlmInput, ProposalWriter, WrittenItem
from app.propose.fake import FakeWriter

WRITERS = {
    "fake": FakeWriter,
}


def get_writer(name: str) -> ProposalWriter:
    """이름으로 제안 작성기를 만든다."""
    if name not in WRITERS:
        raise ValueError(f"모르는 작성기: {name} (가능: {', '.join(WRITERS)})")
    return WRITERS[name]()


__all__ = ["WRITERS", "LlmInput", "ProposalWriter", "WrittenItem", "get_writer"]
