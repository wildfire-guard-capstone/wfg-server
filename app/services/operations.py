"""계산 작업(Operation, 명세 PRED-01 · 05): 예측처럼 오래 걸리는 일의 진행 상태.

메모리에만 둔다 — 서버를 다시 켜면 사라지고, 서버가 여러 개면 서로 모른다.
작업 저장소 · 보존 기간은 명세 Q-05에서 정한다.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

KEEP = 200  # 끝난 작업을 이만큼만 남긴다
ACTIVE = ("QUEUED", "RUNNING")


@dataclass
class Operation:
    id: str
    kind: str  # PREDICTION
    incident_id: int
    user_id: int
    status: str = "QUEUED"  # QUEUED · RUNNING · SUCCEEDED · FAILED
    stage: str | None = None  # SPREAD · RISK · RULES · PROPOSAL_GENERATION · COMMIT
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


_ops: dict[str, Operation] = {}
_lock = threading.Lock()


def start(kind: str, incident_id: int, user_id: int) -> Operation | None:
    """새 작업. 같은 산불에 진행 중인 작업이 있으면 None(중복 실행 막기)."""
    with _lock:
        if any(o.incident_id == incident_id and o.status in ACTIVE for o in _ops.values()):
            return None
        op = Operation(f"op-{uuid.uuid4().hex[:12]}", kind, incident_id, user_id)
        _ops[op.id] = op
        done = [o for o in _ops.values() if o.status not in ACTIVE]
        for old in sorted(done, key=lambda o: o.created_at)[: max(0, len(done) - KEEP)]:
            del _ops[old.id]
        return op


def get(op_id: str) -> Operation | None:
    with _lock:
        return _ops.get(op_id)


def is_running(incident_id: int) -> bool:
    with _lock:
        return any(o.incident_id == incident_id and o.status in ACTIVE for o in _ops.values())


def update(op: Operation, **changes: Any) -> None:
    with _lock:
        for k, v in changes.items():
            setattr(op, k, v)


def clear() -> None:
    """테스트용."""
    with _lock:
        _ops.clear()
