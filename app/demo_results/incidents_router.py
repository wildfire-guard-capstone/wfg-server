"""(김지오 전용 · 임시) 결과 조회 — 산불 · 실측 화선 · 확산 예측(P1~P8) · 대응 제안서.

모두 읽기 전용, 로그인 필요.

권한(화면 목업과 같게): 산불 · 화선 = 통합지휘권자 · 열람자 · 상황 보고자,
예측 = 통합지휘권자 · 열람자, 제안서(근거 원문 포함) = 통합지휘권자만.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db.models.enums import UserRole
from app.demo_results.deps import require_roles, results_store
from app.demo_results.result_schemas import (
    Incident,
    IncidentSummary,
    Perimeter,
    Prediction,
    PredictionSummary,
    Proposal,
    ProposalSummary,
)
from app.demo_results.results import FileResultsStore

READ_INCIDENT = (UserRole.COMMANDER, UserRole.VIEWER, UserRole.REPORTER)
READ_PREDICTION = (UserRole.COMMANDER, UserRole.VIEWER)
READ_PROPOSAL = (UserRole.COMMANDER,)

router = APIRouter(prefix="/api/incidents", tags=["incidents"])
Store = Annotated[FileResultsStore, Depends(results_store)]


def _found[T](value: T | None, what: str) -> T:
    if value is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what}을(를) 찾을 수 없습니다")
    return value


@router.get("", dependencies=[Depends(require_roles(*READ_INCIDENT))])
def list_incidents(store: Store) -> list[IncidentSummary]:
    return store.list_incidents()


@router.get("/{incident_id}", dependencies=[Depends(require_roles(*READ_INCIDENT))])
def get_incident(incident_id: int, store: Store) -> Incident:
    return _found(store.incident(incident_id), "산불")


@router.get("/{incident_id}/perimeters", dependencies=[Depends(require_roles(*READ_INCIDENT))])
def list_perimeters(
    incident_id: int,
    store: Store,
    geometry: Annotated[bool, Query(description="false면 좌표 없이 목록만")] = True,
) -> list[Perimeter]:
    rows = _found(store.perimeters(incident_id), "산불")
    if not geometry:
        rows = [{**r, "geometry": {"type": r["geometry"]["type"]}} for r in rows]
    return rows


@router.get("/{incident_id}/predictions", dependencies=[Depends(require_roles(*READ_PREDICTION))])
def list_predictions(incident_id: int, store: Store) -> list[PredictionSummary]:
    return _found(store.predictions(incident_id), "산불")


@router.get(
    "/{incident_id}/predictions/{prediction_id}",
    dependencies=[Depends(require_roles(*READ_PREDICTION))],
)
def get_prediction(incident_id: int, prediction_id: int, store: Store) -> Prediction:
    _found(store.incident(incident_id), "산불")
    return _found(store.prediction(incident_id, prediction_id), "예측")


@router.get("/{incident_id}/proposals", dependencies=[Depends(require_roles(*READ_PROPOSAL))])
def list_proposals(incident_id: int, store: Store) -> list[ProposalSummary]:
    return _found(store.proposals(incident_id), "산불")


@router.get(
    "/{incident_id}/proposals/{version}",
    dependencies=[Depends(require_roles(*READ_PROPOSAL))],
)
def get_proposal(incident_id: int, version: str, store: Store) -> Proposal:
    """``version`` = ``v1.1`` 같은 버전 또는 ``latest``(가장 최근 제안서)."""
    _found(store.incident(incident_id), "산불")
    return _found(store.proposal(incident_id, version), "제안서")
