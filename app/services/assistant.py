# ruff: noqa: E501
"""AI 어시스턴트(AI-01 · COR-01 · PROP-05): DB → 어시스턴트 Context, 답 저장, 정정 확인 토큰 · 반영 ·
제안서 다시 만들기.

- 질문 → 답(assistant 패키지) → qa_messages 1행(답 · 인용 원문 · 문장별 출처). 근거를 못 찾으면
  이벤트 로그 QA_NO_EVIDENCE(UC-QA-01 E1).
- 정정 → 확인안 + 확인 토큰(사용자 · 산불 · 상황 버전 · 원문 · 변경안에 묶은 서명, 10분). 저장하지 않는다.
- 확인(COR-01) → 토큰 · 상황 버전 · 변경 전 값을 서버 값과 다시 대조 → 한 트랜잭션으로 정정 1행 +
  incidents.situation + 이벤트 로그(SITUATION_CORRECTED) → 커밋 뒤 같은 예측으로 제안서 새 버전
  (작업 PROPOSAL_REGENERATION). 제안 생성이 실패해도 정정은 남는다(UC-QA-02 E2).
"""

import copy
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from sqlalchemy.orm import Session

from agent.clients import LLM, Evidence
from app.api.errors import ApiError
from app.core.config import get_settings
from app.core.security import ALGORITHM, purpose_key
from app.core.time import KST
from app.db.models import Incident, Proposal, SituationCorrection, User
from app.db.models.enums import IncidentStatus, ProposalTrigger
from app.repositories import assistant as repo
from app.repositories import event_logs, incidents
from app.repositories.engine_inputs import incident_info
from app.repositories.proposals import items_of, previous_proposal, save_proposal
from app.services import operations, proposal_compare
from app.services.engine import run_engine
from app.services.prediction import reader_of, writer_from_settings
from assistant import Assistant, Context
from assistant.context import DEPLOYED_LABELS
from assistant.correction import CorrectionError
from assistant.service import AssistantUnavailable
from assistant.tools import change_label

log = logging.getLogger(__name__)
TOKEN_PURPOSE = "correction-preview"
SessionFactory = Callable[[], AbstractContextManager[Session]]


# ── Context ─────────────────────────────────────────────────────────


def version_of(p: Proposal) -> str:
    return f"v{p.perimeter_version}.{p.seq}"


def item_dict(i: Any) -> dict[str, Any]:
    return {
        "item_code": i.item_code,
        "name": (i.detail or {}).get("name"),
        "state": i.state,
        "categories": [c.value for c in i.categories],
        "sections": i.sections,
        "evidence": i.evidence,
        "text": i.text,
        "detail": i.detail,
    }


def build_context(db: Session, incident: Incident, proposal: Proposal | None) -> Context:
    """그 산불 · 그 제안서(없으면 예측 전)로 어시스턴트가 볼 것을 모은다."""
    info = incident_info(db, incident)
    info |= {"id": incident.id, "current_stage": incident.current_stage.value}
    ctx = Context(
        incident=info,
        reader=reader_of(db, incident.id),
        situation=copy.deepcopy(incident.situation or {}),
        villages=repo.village_directory(db, incidents.sigungu_of(db, incident)),
        revision=incidents.situation_revision(db, incident.id),
    )
    if proposal is not None:
        ctx.proposal = {
            "id": proposal.id,
            "version": version_of(proposal),
            "trigger": proposal.trigger.value,
            "created_at": proposal.created_at.astimezone(KST).isoformat(),
            "base_at": (proposal.context.get("time") or {}).get("base_at"),
        }
        ctx.bundles = proposal.context or {}
        rows = items_of(db, proposal.id)
        ctx.items = [item_dict(i) for i in rows]
        prev = previous_proposal(db, proposal)
        if prev is not None:
            prev_rows = items_of(db, prev.id)
            ctx.previous = {
                "version": version_of(prev),
                "items": [
                    {
                        "item_code": i.item_code,
                        "categories": [c.value for c in i.categories],
                        "text": i.text,
                    }
                    for i in prev_rows
                ],
                # PROP-04 비교와 같은 「변경」 판단(표현만 바뀐 것은 변경 아님)
                "changes": proposal_compare.changes(prev_rows, rows),
            }
    ctx.corrections = [
        {
            "id": c.id,
            "input_text": c.input_text,
            "changes": c.changes,
            "created_at": c.created_at.astimezone(KST).strftime("%H:%M"),
        }
        for c in repo.list_corrections(db, incident.id, before_id=None, limit=5)[:5]
    ]
    return ctx


def make_assistant(ctx: Context) -> Assistant:
    """설정(ASSISTANT)으로 LLM을 붙인다: rules = 규칙만, exaone = vLLM + 근거 서버."""
    s = get_settings()
    if s.assistant == "rules":
        return Assistant(ctx)
    if s.assistant != "exaone":
        raise ValueError(f"모르는 어시스턴트 설정: {s.assistant} (가능: rules, exaone)")
    llm = LLM(url=s.llm_url, model=s.llm_model, timeout=s.llm_timeout_s)
    evidence = Evidence(url=s.evidence_url, timeout=30) if s.evidence_url else None
    return Assistant(ctx, llm, evidence)


# ── 확인 토큰 ────────────────────────────────────────────────────────


def issue_token(
    *,
    user_id: int,
    incident_id: int,
    revision: str,
    input_text: str,
    changes: list[dict[str, Any]],
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """확인안 토큰(HS256, 정정 용도 키). 화면은 changes를 다시 보내지 않고 이 토큰만 보낸다."""
    now = now or datetime.now(UTC)
    exp = now + timedelta(seconds=get_settings().correction_ttl_s)
    claims = {
        "typ": TOKEN_PURPOSE,
        "sub": str(user_id),
        "inc": incident_id,
        "rev": revision,
        "txt": input_text,
        "chg": changes,
        "iat": now,
        "exp": exp,
    }
    return jwt.encode(claims, purpose_key(TOKEN_PURPOSE), algorithm=ALGORITHM), exp


def read_token(token: str, *, user_id: int, incident_id: int) -> dict[str, Any]:
    try:
        claims = jwt.decode(token, purpose_key(TOKEN_PURPOSE), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(
            422,
            "EXPIRED_CONFIRMATION_TOKEN",
            "확인 시간이 지났습니다. 상황 변화를 다시 입력해 주십시오.",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise ApiError(422, "INVALID_CONFIRMATION_TOKEN", "확인안이 올바르지 않습니다.") from exc
    if (
        claims.get("typ") != TOKEN_PURPOSE
        or claims.get("sub") != str(user_id)
        or claims.get("inc") != incident_id
    ):
        raise ApiError(422, "INVALID_CONFIRMATION_TOKEN", "이 사용자 · 산불의 확인안이 아닙니다.")
    return claims


# ── 질문 · 정정 입력(AI-01) ───────────────────────────────────────────


def citation_out(qa_id: int, c: dict[str, Any]) -> dict[str, Any]:
    page = c.get("page")
    return {
        "id": f"{qa_id}-{c['k']}",
        "k": c["k"],
        "chunk_id": c["chunk_id"],
        "chunk_code": c["chunk_id"],
        "label": c.get("label"),
        "doc_name": c.get("doc_name"),
        "page": int(page) if str(page or "").isdigit() else None,
        "section": c.get("section"),
        "origin": c.get("origin"),
        "text": c.get("text"),
    }


def qa_out(row: Any) -> dict[str, Any]:
    d = row.detail or {}
    return {
        "id": str(row.id),
        "incident_id": str(row.incident_id),
        "user_id": str(row.user_id),
        "proposal_id": str(row.proposal_id) if row.proposal_id else None,
        "item_code": row.item_code,
        "question": row.question,
        "answer": row.answer,
        "citations": [citation_out(row.id, c) for c in row.evidence or []],
        "route": d.get("route"),
        "sentences": d.get("sentences", []),
        "insufficient": d.get("insufficient", []),
        "no_evidence": d.get("no_evidence", False),
        "created_at": row.created_at.astimezone(KST).isoformat(),
    }


def ask(
    db: Session,
    user: User,
    incident: Incident,
    *,
    input_text: str,
    proposal_id: int | None,
    item_code: str | None,
) -> dict[str, Any]:
    """AI-01. 질문이면 답을 저장해 {type: ANSWER, message}, 정정이면 확인안(저장 안 함)."""
    if proposal_id is not None:
        proposal = db.get(Proposal, proposal_id)
        if proposal is None or proposal.incident_id != incident.id:
            raise ApiError(404, "NOT_FOUND", "제안서를 찾을 수 없습니다.")
    else:
        proposal = incidents.latest_proposal(db, incident.id)
    ctx = build_context(db, incident, proposal)
    try:
        out = make_assistant(ctx).handle(input_text, item_code)
    except CorrectionError as e:
        raise ApiError(e.status, e.code, e.message) from e
    except AssistantUnavailable as e:
        log.warning("어시스턴트 LLM · 근거 서버 연결 실패: %s", e)
        raise ApiError(
            503, "AI_UNAVAILABLE", "AI 서버에 연결할 수 없습니다. 잠시 뒤 다시 시도해 주십시오."
        ) from e
    trace = out.pop("trace", {})
    if out["type"] == "CORRECTION_PREVIEW":
        changes = out["changes"]
        token, exp = issue_token(
            user_id=user.id,
            incident_id=incident.id,
            revision=ctx.revision,
            input_text=input_text,
            changes=changes,
        )
        return {
            "type": "CORRECTION_PREVIEW",
            "confirmation_token": token,
            "expires_at": exp.astimezone(KST).isoformat(),
            "situation_revision": ctx.revision,
            "input_text": input_text,
            "changes": changes,
            "ignored": out.get("ignored", []),
            "notes": out.get("notes", []),
            "summary": out.get("summary", ""),
            "proposal_regenerates": proposal is not None,
        }
    if out["type"] == "UNSUPPORTED":
        return {"type": "UNSUPPORTED", "reason": out["reason"], "ignored": out.get("ignored", [])}
    detail = {
        k: out.get(k) for k in ("route", "sentences", "insufficient", "tools", "no_evidence")
    } | {
        "assistant": get_settings().assistant,
        "seconds": trace.get("seconds"),
        "llm_calls": len(trace.get("llm_calls") or []),
    }
    row = repo.add_qa(
        db,
        incident_id=incident.id,
        user_id=user.id,
        proposal_id=proposal.id if proposal else None,
        item_code=item_code,
        question=input_text,
        answer=out["answer"],
        evidence=out.get("citations", []),
        detail=detail,
    )
    if out.get("no_evidence") or out.get("insufficient"):
        event_logs.add(
            db,
            "QA_NO_EVIDENCE",
            user_id=user.id,
            incident_id=incident.id,
            details={
                "qa_id": row.id,
                "full": bool(out.get("no_evidence")),
                "missing": out.get("insufficient", []),
            },
        )
    db.commit()
    return {"type": "ANSWER", "message": qa_out(row)}


# ── 정정 확인(COR-01) ─────────────────────────────────────────────────


def _current(situation: dict[str, Any], ch: dict[str, Any]) -> Any:
    if ch["target"] == "village":
        return bool(
            ((situation.get("villages") or {}).get(str(ch["target_id"])) or {}).get(
                ch["item"], False
            )
        )
    if ch["target"] == "deployed":
        group, _, key = ch["item"].partition(".")
        g = (situation.get("deployed") or {}).get(group) or {}
        if key == "total":
            return int(g.get("total", sum(int(v) for k, v in g.items() if k != "total")))
        return int(g.get(key, 0))
    if ch["target"] == "situation":
        return bool(situation.get(ch["item"], False))
    raise ValueError(f"정정할 수 없는 대상: {ch['target']}")


ALLOWED = {
    "village": {"evacuated", "evac_order", "pre_evac_done"},
    "situation": {"disaster_sms_sent"},
}


def _allowed(ch: dict[str, Any]) -> bool:
    if ch.get("target") == "deployed":
        group, _, key = str(ch.get("item", "")).partition(".")
        return group in DEPLOYED_LABELS and (key == "total" or key in DEPLOYED_LABELS[group])
    return ch.get("item") in ALLOWED.get(ch.get("target"), set())


def apply_changes(
    situation: dict[str, Any], changes: list[dict[str, Any]], at: str
) -> dict[str, Any]:
    """정정 → 새 situation(원본은 그대로). at = 보고 시각 「HH:MM」(엔진의 reported_at)."""
    new = copy.deepcopy(situation or {})
    for ch in changes:
        if ch["target"] == "village":
            v = new.setdefault("villages", {}).setdefault(str(ch["target_id"]), {})
            v[ch["item"]] = bool(ch["after"])
            v["reported_at"] = at
        elif ch["target"] == "deployed":
            group, _, key = ch["item"].partition(".")
            dep = new.setdefault("deployed", {})
            dep.setdefault(group, {})[key] = int(ch["after"])
            dep["reported_at"] = at
        elif ch["target"] == "situation":
            new[ch["item"]] = bool(ch["after"])
    for g in (new.get("deployed") or {}).values():  # 명세: 묶음이 있으면 total 필수
        if isinstance(g, dict) and "total" not in g:
            g["total"] = sum(int(v) for v in g.values())
    return new


def confirm(
    db: Session, user: User, incident_id: int, token: str
) -> tuple[SituationCorrection, str]:
    """COR-01 앞부분: 대조 · 반영 · 커밋. (정정 행, 새 상황 버전). 제안서 작업은 부르는 쪽이 시작한다."""
    claims = read_token(token, user_id=user.id, incident_id=incident_id)
    incident = db.get(Incident, incident_id, with_for_update=True)  # 동시 확인 막기
    if incident.status is IncidentStatus.ENDED:
        raise ApiError(409, "INCIDENT_ENDED", "종료된 산불은 상황을 정정할 수 없습니다.")
    if incidents.situation_revision(db, incident_id) != claims["rev"]:
        raise ApiError(
            409,
            "STALE_SITUATION",
            "확인안을 만든 뒤 상황이 바뀌었습니다(이미 반영했거나 다른 정정이 있음). 다시 입력해 주십시오.",
        )
    changes = claims["chg"]
    if not changes or not all(_allowed(ch) for ch in changes):
        raise ApiError(422, "INVALID_CONFIRMATION_TOKEN", "확인안의 변경 항목이 올바르지 않습니다.")
    for ch in changes:
        if _current(incident.situation or {}, ch) != ch["before"]:
            raise ApiError(
                409,
                "STALE_SITUATION",
                f"「{ch.get('label') or change_label(ch)}」의 지금 값이 확인안과 다릅니다. 다시 입력해 주십시오.",
            )
    at = datetime.now(KST).strftime("%H:%M")
    incident.situation = apply_changes(incident.situation or {}, changes, at)
    row = repo.add_correction(
        db, incident_id=incident_id, user_id=user.id, input_text=claims["txt"], changes=changes
    )
    event_logs.add(
        db,
        "SITUATION_CORRECTED",
        user_id=user.id,
        incident_id=incident_id,
        details={"correction_id": row.id, "changes": len(changes)},
    )
    db.commit()
    return row, incidents.situation_revision(db, incident_id)


def correction_out(row: SituationCorrection) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "incident_id": str(row.incident_id),
        "user_id": str(row.user_id),
        "input_text": row.input_text,
        "changes": [
            {**ch, "target_id": str(ch["target_id"])} if ch.get("target_id") is not None else ch
            for ch in row.changes
        ],
        "created_at": row.created_at.astimezone(KST).isoformat(),
    }


# ── 제안서 다시 만들기(정정 뒤 · PROP-05) ───────────────────────────────


def run_regeneration_operation(
    op: operations.Operation,
    *,
    prediction_id: int,
    correction_id: int,
    session_factory: SessionFactory,
) -> None:
    """같은 예측 + 지금 상황 → 규칙 엔진(RULES) → 제안 작성(PROPOSAL_GENERATION) → 저장(COMMIT).

    저장 직전에 종료 · 새 예측 · 더 새로운 정정이 있으면 저장하지 않는다(옛 상황으로 만든 제안서를
    최신으로 보이지 않게). 실패하면 PROPOSAL_FAILED만 남기고, 정정은 그대로다.
    """
    operations.update(op, status="RUNNING", stage="RULES")
    with session_factory() as db:
        try:
            llm_input = run_engine(
                db,
                incident_id=op.incident_id,
                reader=reader_of(db, op.incident_id),
                prediction_id=prediction_id,
            )
            operations.update(op, stage="PROPOSAL_GENERATION")
            written = writer_from_settings().write(llm_input)
            operations.update(op, stage="COMMIT")
            incident = db.get(Incident, op.incident_id)
            if incident.status is IncidentStatus.ENDED:
                raise ApiError(409, "INCIDENT_ENDED", "계산하는 동안 산불이 종료되었습니다.")
            latest = incidents.latest_prediction(db, op.incident_id)
            if latest is None or latest[0] != prediction_id:
                raise ApiError(409, "STALE_PREDICTION", "계산하는 동안 새 예측이 생겼습니다.")
            last = incidents.latest_correction(db, op.incident_id)
            if last is not None and last[0] != correction_id:
                raise ApiError(409, "STALE_SITUATION", "계산하는 동안 다른 정정이 들어왔습니다.")
            proposal_id = save_proposal(
                db,
                prediction_id=prediction_id,
                llm_input=llm_input,
                written=written,
                user_id=op.user_id,
                trigger=ProposalTrigger.CORRECTION,
                correction_id=correction_id,
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001 — 어떤 실패든 작업 실패로 알린다
            db.rollback()
            if isinstance(exc, ApiError):
                error = {"code": exc.code, "message": exc.message}
            else:
                log.exception("제안서 다시 만들기 실패 (%s, %s 단계)", op.id, op.stage)
                error = {
                    "code": "PROPOSAL_FAILED",
                    "message": "제안서를 만드는 중 오류가 발생했습니다.",
                }
            event_logs.add(
                db,
                "PROPOSAL_FAILED",
                user_id=op.user_id,
                incident_id=op.incident_id,
                details={"operation_id": op.id, "correction_id": correction_id, **error},
            )
            db.commit()
            operations.update(op, status="FAILED", error=error)
            return
    operations.update(
        op,
        status="SUCCEEDED",
        result={
            "proposal_id": str(proposal_id),
            "prediction_id": str(prediction_id),
            "correction_id": str(correction_id),
        },
    )


def start_regeneration(
    db: Session, user: User, incident_id: int
) -> tuple[operations.Operation | None, int | None, int | None]:
    """(작업, 예측 id, 정정 id). 예측이 없으면 작업 없음(다음 예측 때 반영), 다른 작업이 돌면 None."""
    latest = incidents.latest_prediction(db, incident_id)
    last = incidents.latest_correction(db, incident_id)
    if latest is None or last is None:
        return None, None, None
    op = operations.start("PROPOSAL_REGENERATION", incident_id, user.id)
    return op, latest[0], last[0]
