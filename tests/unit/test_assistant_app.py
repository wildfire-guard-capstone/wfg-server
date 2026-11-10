"""앱 쪽 어시스턴트(app/services/assistant.py) — DB 없이 되는 것: 확인 토큰, 정정 반영."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.api.errors import ApiError
from app.core.security import decode_access_token
from app.services.assistant import _allowed, _current, apply_changes, issue_token, read_token

CHANGES = [
    {"target": "village", "target_id": "101", "item": "evacuated", "before": False, "after": True},
    {"target": "deployed", "item": "helicopter.forest", "before": 5, "after": 7},
    {"target": "deployed", "item": "helicopter.total", "before": 23, "after": 25},
    {"target": "situation", "item": "disaster_sms_sent", "before": False, "after": True},
]
SITUATION = {
    "villages": {"102": {"alert_at": "12:40"}},
    "deployed": {"helicopter": {"total": 23, "forest": 5, "local": 16, "fire": 2}},
}


def test_token_roundtrip_and_binding():
    token, exp = issue_token(
        user_id=3, incident_id=9, revision="rev-0", input_text="괴산리 대피 완료", changes=CHANGES
    )
    claims = read_token(token, user_id=3, incident_id=9)
    assert claims["chg"] == CHANGES and claims["rev"] == "rev-0"
    assert claims["txt"] == "괴산리 대피 완료"
    for user, inc in ((4, 9), (3, 10)):
        with pytest.raises(ApiError) as e:
            read_token(token, user_id=user, incident_id=inc)
        assert e.value.code == "INVALID_CONFIRMATION_TOKEN"


def test_token_tamper_expiry_and_not_a_login_token():
    token, _ = issue_token(user_id=3, incident_id=9, revision="rev-0", input_text="x", changes=[])
    head, body, sig = token.split(".")
    with pytest.raises(ApiError) as e:
        read_token(f"{head}.{body}.{sig[:-2]}AA", user_id=3, incident_id=9)
    assert e.value.code == "INVALID_CONFIRMATION_TOKEN"
    old, _ = issue_token(
        user_id=3,
        incident_id=9,
        revision="rev-0",
        input_text="x",
        changes=[],
        now=datetime.now(UTC) - timedelta(hours=1),
    )
    with pytest.raises(ApiError) as e:
        read_token(old, user_id=3, incident_id=9)
    assert e.value.code == "EXPIRED_CONFIRMATION_TOKEN"
    with pytest.raises(jwt.InvalidTokenError):  # 정정 토큰은 로그인 토큰으로 못 씀
        decode_access_token(token)


def test_apply_changes_keeps_original_and_sets_report_time():
    new = apply_changes(SITUATION, CHANGES, "13:05")
    assert SITUATION["deployed"]["helicopter"]["forest"] == 5  # 원본 그대로
    assert new["villages"]["101"] == {"evacuated": True, "reported_at": "13:05"}
    assert new["villages"]["102"] == {"alert_at": "12:40"}
    assert new["deployed"]["helicopter"] == {"total": 25, "forest": 7, "local": 16, "fire": 2}
    assert new["deployed"]["reported_at"] == "13:05"
    assert new["disaster_sms_sent"] is True
    added = apply_changes(
        {}, [{"target": "deployed", "item": "crew.other", "before": 0, "after": 50}], "13:05"
    )
    assert added["deployed"]["crew"] == {"other": 50, "total": 50}


def test_current_values_and_allowed_items():
    assert [_current(SITUATION, c) for c in CHANGES] == [False, 5, 23, False]
    assert all(_allowed(c) for c in CHANGES)
    assert not _allowed({"target": "incident", "item": "current_stage"})
    assert not _allowed({"target": "deployed", "item": "helicopter.navy"})
    assert not _allowed({"target": "village", "item": "injured"})


def test_engine_reads_applied_situation():
    """반영한 상황을 엔진 입력 어댑터가 그대로 읽는다(기관 칸 합 = 엔진 합계)."""
    from app.repositories.engine_inputs import deployed_of, village_fields

    new = apply_changes(SITUATION, CHANGES, "13:05")
    d = deployed_of(new)
    assert sum(d.helicopter.values()) == 25 and d.reported_at == "13:05"
    assert village_fields(new)[101].evacuated is True
