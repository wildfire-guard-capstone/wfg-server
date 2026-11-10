# ruff: noqa: E501
"""AI-01 질의응답 · 정정 확인안, AI-02 질의응답 이력, COR-01 정정 저장, COR-02 정정 이력,
PROP-05 정정 미반영 제안 재생성. 모두 통합지휘권자만(분석서 F-29 · F-30)."""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Header, Request

from app.api.deps import Db, SessionFactory, require_roles
from app.api.envelope import Envelope, Page, ok
from app.api.errors import ApiError
from app.api.pagination import LIMIT, cut, decode_cursor
from app.db.models.enums import IncidentStatus
from app.repositories import assistant as repo
from app.repositories import incidents
from app.routers.incidents import IncidentId, load_incident
from app.schemas.assistant import AssistantMessageIn, CorrectionConfirmIn, RegenerationIn
from app.schemas.prediction import OperationOut
from app.services import assistant as svc
from app.services import operations

router = APIRouter(prefix="/incidents/{incident_id}", tags=["AI 어시스턴트 · 상황 정정"])
Commander = require_roles("COMMANDER")


@router.post("/assistant/messages", response_model=Envelope[dict[str, Any]])
def post_message(
    incident_id: IncidentId, body: AssistantMessageIn, request: Request, user: Commander, db: Db
):
    """AI-01. 질문이면 답(저장, type ANSWER), 상황 변화면 정정 확인안(저장 안 함, type
    CORRECTION_PREVIEW), 정정할 수 없는 말이면 type UNSUPPORTED. 여러 턴 문맥은 없다."""
    incident = load_incident(db, user, incident_id)
    data = svc.ask(
        db,
        user,
        incident,
        input_text=body.input_text,
        proposal_id=body.proposal_id,
        item_code=body.item_code,
    )
    return ok(request, data)


@router.get("/assistant/messages", response_model=Envelope[Page[dict[str, Any]]])
def list_messages(
    incident_id: IncidentId,
    request: Request,
    user: Commander,
    db: Db,
    cursor: str | None = None,
    limit: int = LIMIT,
):
    """AI-02. 저장한 질문 · 답만 id 내림차순(정정 입력 · 취소한 확인안은 없음)."""
    load_incident(db, user, incident_id)
    after = decode_cursor(cursor, 1)
    rows = repo.list_qa(db, incident_id, before_id=int(after[0]) if after else None, limit=limit)
    items, next_cursor, has_more = cut(rows, limit, lambda r: (r.id,))
    page = Page(items=[svc.qa_out(r) for r in items], next_cursor=next_cursor, has_more=has_more)
    return ok(request, page)


def _start(
    background: BackgroundTasks, db: Db, user: Any, incident_id: int, session_factory: Any
) -> operations.Operation | None:
    op, prediction_id, correction_id = svc.start_regeneration(db, user, incident_id)
    if op is not None:
        background.add_task(
            svc.run_regeneration_operation,
            op,
            prediction_id=prediction_id,
            correction_id=correction_id,
            session_factory=session_factory,
        )
    return op


@router.post("/corrections", status_code=201, response_model=Envelope[dict[str, Any]])
def confirm_correction(
    incident_id: IncidentId,
    body: CorrectionConfirmIn,
    request: Request,
    background: BackgroundTasks,
    user: Commander,
    db: Db,
    session_factory: SessionFactory,
    idempotency_key: Annotated[str | None, Header()] = None,
):
    """COR-01. 확인안 토큰만 받는다(화면이 변경안을 다시 쓰지 않음). 정정 저장이 먼저이고, 예측이
    있으면 같은 예측으로 제안서를 다시 만드는 작업을 돌려준다. 작업을 시작하지 못해도(다른 계산 중)
    정정은 저장되고 proposal_stale = true · proposal_operation = null → PROP-05로 다시 한다.
    같은 토큰을 두 번 보내면 상황 버전이 바뀌어 409 STALE_SITUATION(중복 저장 없음)."""
    load_incident(db, user, incident_id)
    row, revision = svc.confirm(db, user, incident_id, body.confirmation_token)
    has_prediction = incidents.latest_prediction(db, incident_id) is not None
    op = _start(background, db, user, incident_id, session_factory) if has_prediction else None
    return ok(
        request,
        {
            "correction": svc.correction_out(row),
            "situation_revision": revision,
            "proposal_stale": has_prediction,
            "proposal_operation": OperationOut.of(op).model_dump() if op else None,
        },
    )


@router.get("/corrections", response_model=Envelope[Page[dict[str, Any]]])
def list_corrections(
    incident_id: IncidentId,
    request: Request,
    user: Commander,
    db: Db,
    cursor: str | None = None,
    limit: int = LIMIT,
):
    """COR-02. 확인한 정정만 id 내림차순."""
    load_incident(db, user, incident_id)
    after = decode_cursor(cursor, 1)
    rows = repo.list_corrections(
        db, incident_id, before_id=int(after[0]) if after else None, limit=limit
    )
    items, next_cursor, has_more = cut(rows, limit, lambda r: (r.id,))
    page = Page(
        items=[svc.correction_out(r) for r in items], next_cursor=next_cursor, has_more=has_more
    )
    return ok(request, page)


@router.post("/proposal-regenerations", status_code=202, response_model=Envelope[OperationOut])
def regenerate(
    incident_id: IncidentId,
    body: RegenerationIn,
    request: Request,
    background: BackgroundTasks,
    user: Commander,
    db: Db,
    session_factory: SessionFactory,
    idempotency_key: Annotated[str | None, Header()] = None,
):
    """PROP-05. 새 예측 없이 최신 예측 + 지금 상황으로 제안서를 다시 만든다(정정 뒤 생성 실패 재시도)."""
    incident = load_incident(db, user, incident_id)
    if incident.status is IncidentStatus.ENDED:
        raise ApiError(409, "INCIDENT_ENDED", "종료된 산불은 제안서를 새로 만들지 않습니다.")
    latest = incidents.latest_prediction(db, incident_id)
    if latest is None:
        raise ApiError(422, "NO_PREDICTION", "예측이 없습니다. 확산 예측을 먼저 실행하세요.")
    if latest[0] != body.expected_prediction_id:
        raise ApiError(409, "STALE_PREDICTION", "예측이 바뀌었습니다. 새로 고친 뒤 다시 하세요.")
    if incidents.situation_revision(db, incident_id) != body.situation_revision:
        raise ApiError(409, "STALE_SITUATION", "상황이 바뀌었습니다. 새로 고친 뒤 다시 하세요.")
    proposal = incidents.latest_proposal(db, incident_id)
    correction = incidents.latest_correction(db, incident_id)
    if correction is None or (proposal is not None and correction[1] <= proposal.created_at):
        raise ApiError(
            409, "REGENERATION_NOT_NEEDED", "제안서가 이미 최신 상황을 반영하고 있습니다."
        )
    op = _start(background, db, user, incident_id, session_factory)
    if op is None:
        raise ApiError(409, "OPERATION_RUNNING", "이 산불의 계산이 이미 진행 중입니다.")
    return ok(request, OperationOut.of(op))
