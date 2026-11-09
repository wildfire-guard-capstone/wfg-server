"""PROP-01 버전 목록 · PROP-02 최신 제안서 · PROP-03 특정 버전 · PROP-04 직전 버전 비교.

모두 지휘권자만(06 결정). 다른 산불의 제안서 번호거나 관할 밖이면 404.
"""

from typing import Annotated

from fastapi import APIRouter, Path, Request
from sqlalchemy.orm import Session

from app.api.deps import Db, require_roles
from app.api.envelope import Envelope, Page, ok
from app.api.errors import not_found
from app.api.pagination import LIMIT, cut, decode_cursor
from app.db.models import Proposal
from app.repositories import incidents, proposals
from app.routers.incidents import IncidentId, load_incident
from app.schemas.proposal import ProposalDiffOut, ProposalOut, ProposalVersionOut
from app.services import proposal_view

router = APIRouter(prefix="/incidents", tags=["제안서"])
Commander = require_roles("COMMANDER")
ProposalId = Annotated[int, Path(gt=0)]


def _load(db: Session, incident_id: int, proposal_id: int) -> Proposal:
    p = db.get(Proposal, proposal_id)
    if p is None or p.incident_id != incident_id:
        raise not_found("제안서를 찾을 수 없습니다.")
    return p


@router.get("/{incident_id}/proposals", response_model=Envelope[Page[ProposalVersionOut]])
def list_proposals(
    incident_id: IncidentId,
    request: Request,
    user: Commander,
    db: Db,
    cursor: str | None = None,
    limit: int = LIMIT,
):
    """PROP-01. 최신이 맨 앞(id 내림차순)."""
    load_incident(db, user, incident_id)
    after = decode_cursor(cursor, 1)
    rows = proposals.list_proposals(
        db, incident_id, before_id=int(after[0]) if after else None, limit=limit
    )
    _, next_cursor, has_more = cut(rows, limit, key=lambda p: (p.id,))
    items = proposal_view.version_rows(db, rows, limit)
    return ok(request, {"items": items, "next_cursor": next_cursor, "has_more": has_more})


@router.get("/{incident_id}/proposals/latest", response_model=Envelope[ProposalOut | None])
def latest_proposal(incident_id: IncidentId, request: Request, user: Commander, db: Db):
    """PROP-02. 그 산불에서 마지막으로 저장된 제안서. 아직 없으면 data: null."""
    load_incident(db, user, incident_id)
    p = incidents.latest_proposal(db, incident_id)
    return ok(request, None if p is None else proposal_view.proposal_out(db, p))


@router.get("/{incident_id}/proposals/{proposal_id}", response_model=Envelope[ProposalOut])
def get_proposal(
    incident_id: IncidentId, proposal_id: ProposalId, request: Request, user: Commander, db: Db
):
    """PROP-03. PROP-02와 같은 모양. 「변경」은 그 버전의 바로 앞 버전과 비교한 값."""
    load_incident(db, user, incident_id)
    return ok(request, proposal_view.proposal_out(db, _load(db, incident_id, proposal_id)))


@router.get("/{incident_id}/proposals/{proposal_id}/diff", response_model=Envelope[ProposalDiffOut])
def diff_proposal(
    incident_id: IncidentId, proposal_id: ProposalId, request: Request, user: Commander, db: Db
):
    """PROP-04. 바로 앞 버전과 비교해 바뀐 항목만. 첫 버전이면 빈 결과."""
    load_incident(db, user, incident_id)
    return ok(request, proposal_view.diff_out(db, _load(db, incident_id, proposal_id)))
