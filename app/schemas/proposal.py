"""제안서 조회 DTO: PROP-01 버전 목록 · PROP-02/03 제안서 · PROP-04 직전 버전 비교
(06 제안서 조회 API). 값은 영문 코드(명세 3.2 · 다른 API와 같음, 11/10) — 화면 글자는 프론트가."""

from typing import Any, Literal

from pydantic import BaseModel

from app.schemas.common import DateTimeOut, Id

StageCode = Literal["INITIAL", "STAGE1", "STAGE2"]
TriggerCode = Literal["PREDICTION", "CORRECTION"]  # 예측 갱신 · 상황 정정
CategoryCode = Literal["IMMEDIATE", "STANDBY", "CONSULT", "REQUEST"]  # 즉시 · 대기 · 협의 · 요청
# 생성 · 일부 생성 · 실패 · 재사용 · 해당 없음
StateCode = Literal["GENERATED", "PARTIAL", "FAILED", "REUSED", "NOT_TRIGGERED"]


class ProposalVersionOut(BaseModel):
    """PROP-01 한 줄(버전 하나)."""

    id: Id
    version: str  # 「v1.2」
    trigger: TriggerCode
    created_at: DateTimeOut
    judged_stage: StageCode
    predicted_stage: StageCode
    category_counts: dict[CategoryCode, int]  # 구분별 항목 수
    changed_count: int | None  # 바로 앞 버전 대비 바뀐 항목 수. 첫 버전은 null


class EvidenceOut(BaseModel):
    chunk_id: str
    label: str | None
    doc_name: str | None
    section: str | None
    page: int | None
    origin: str | None  # 판정 근거 · 검색
    text: str | None  # 원문 전체(EVD-01 대신)


class SectionOut(BaseModel):
    title: str
    # paragraph · list · table(rows = 열 순서 배열) · note · failed — 06 PROP-02 블록 표
    blocks: list[dict[str, Any]]


class ProposalItemOut(BaseModel):
    item_code: str  # S1 ~ E6
    name: str
    axis: Literal["SUPPRESSION", "EVACUATION"]  # 진화 · 대피
    state: StateCode
    categories: list[CategoryCode]
    finding: str  # 판정 한 줄
    withheld: list[dict[str, Any]] | None = None  # 엔진이 판단하지 않은 것
    # 변경 · 같음(표현만 바뀐 것 포함) · 신규. 첫 버전 · 해당 없음이면 null
    change_type: Literal["CHANGED", "SAME", "NEW"] | None
    sections: list[SectionOut]
    evidence: list[EvidenceOut]


class ProposalOut(BaseModel):
    """PROP-02 최신 · PROP-03 특정 버전(같은 모양). items는 늘 12칸, S1 → E6."""

    id: Id
    version: str
    trigger: TriggerCode
    created_at: DateTimeOut
    base_at: DateTimeOut  # 예측 기준 시각(화선 관측 시각)
    prediction_id: Id
    judged_stage: StageCode
    predicted_stage: StageCode
    items: list[ProposalItemOut]


class Pair(BaseModel):
    before: Any
    after: Any


class ParagraphSide(BaseModel):
    category: CategoryCode | None
    body: str | None


class ProposalChangeOut(BaseModel):
    change: Literal["ADDED", "REMOVED", "MODIFIED"]  # 새 · 없어진 · 바뀐 제안
    topic: str | None
    before: ParagraphSide | None
    after: ParagraphSide | None


class RowsDiff(BaseModel):
    added: list[str]
    removed: list[str]


class ItemDiffOut(BaseModel):
    item_code: str
    name: str
    change: Literal["CHANGED", "NEW", "REMOVED"]  # 변경 · 신규 · 빠짐
    categories: Pair  # 신규면 before null, 빠짐이면 after null
    finding: Pair | None  # 같으면 null
    proposals: list[ProposalChangeOut]  # 바뀐 제안만
    rows: RowsDiff  # 목록 · 표 · ※ 줄
    note: str | None  # 이번 버전에서 실패했으면 그 안내


class DiffCounts(BaseModel):
    changed: int
    wording_only: int
    same: int


class ProposalDiffOut(BaseModel):
    """PROP-04. 첫 버전이면 previous_version · cause · counts null, items 빈 목록."""

    version: str
    previous_version: str | None
    trigger: TriggerCode
    created_at: DateTimeOut
    cause: str | None
    counts: DiffCounts | None
    items: list[ItemDiffOut]  # 바뀐 항목만, S1 → E6
    wording_only_items: list[str]
    same_items: list[str]
