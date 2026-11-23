"""기관별 접수 기록(11/23): INC-02 요청에 담아 저장하고 INC-03 응답에 돌려준다(DB 없이)."""

import pytest
from pydantic import ValidationError

from app.schemas.incident import IncidentCreateIn, IncidentOut, IntakeRecordIn

BODY = {
    "ignition_point": {"type": "Point", "coordinates": [128.60681, 36.37819]},
    "address": "경상북도 의성군 안평면 괴산리 산64 일원",
    "reported_at": "2026-11-23T13:32:00+09:00",
    "reporter_name": "김영호",
    "reporter_phone": "010-0000-0000",
}
ROW = {"org_name": "의성소방서", "received_at": "2026-11-23T13:32:00+09:00", "channel": "119 신고"}


def test_intake_records_default_to_empty():
    assert IncidentCreateIn(**BODY).intake_records == []


def test_intake_records_are_accepted_in_order():
    rows = [ROW, {"org_name": "의성군 산림과", "received_at": "2026-11-23T13:40:00+09:00"}]
    body = IncidentCreateIn(**BODY, intake_records=rows)
    assert [r.org_name for r in body.intake_records] == ["의성소방서", "의성군 산림과"]
    assert body.intake_records[1].channel is None


@pytest.mark.parametrize(
    "row",
    [
        {**ROW, "org_name": ""},  # 기관명은 필수
        {**ROW, "org_name": "가" * 101},
        {**ROW, "channel": "가" * 31},
        {**ROW, "received_at": "2026-11-23T13:32:00"},  # 시간대 없는 시각
        {**ROW, "note": "모르는 칸"},  # 명시하지 않은 칸은 422(명세 2.1)
    ],
)
def test_bad_intake_record_is_rejected(row):
    with pytest.raises(ValidationError):
        IntakeRecordIn(**row)


def test_too_many_intake_records_are_rejected():
    with pytest.raises(ValidationError):
        IncidentCreateIn(**BODY, intake_records=[ROW] * 21)


def test_incident_out_always_carries_intake_records():
    assert IncidentOut.model_fields["intake_records"].is_required()


# 신고자(11/23): 신고 기관명 · 신고자 성명 중 하나는 필수, 성명은 비워도 된다
def test_org_alone_is_enough():
    body = {**BODY, "reporter_name": None, "reporter_org": "의성군 산림과"}
    assert IncidentCreateIn(**body).reporter_name is None


def test_name_alone_is_enough():
    assert IncidentCreateIn(**BODY).reporter_org is None


@pytest.mark.parametrize("org,name", [(None, None), ("  ", None), (None, " "), ("", "")])
def test_neither_org_nor_name_is_rejected(org, name):
    body = {**BODY, "reporter_org": org, "reporter_name": name}
    with pytest.raises(ValidationError, match="신고 기관명과 신고자 성명 중 하나"):
        IncidentCreateIn(**body)
