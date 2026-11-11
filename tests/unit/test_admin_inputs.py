"""관리 API(USR-03 · RES-03 · 04 · BASE-02 · 03) 입력 검사와 명세 경로 등록 — DB 없이."""

import pytest
from pydantic import ValidationError

from app.api.errors import ApiError
from app.main import app
from app.schemas.resource import BaseUpdateIn, ResourceCreateIn, ResourceUpdateIn
from app.schemas.user import UserUpdateIn
from app.services.resource_admin import check_attrs

# 명세 v0.5의 P3 12개(method, 경로)
P3_ROUTES = [
    ("get", "/api/v1/map/entities/{entity_type}/{entity_id}"),  # MAP-03
    ("get", "/api/v1/admin-areas"),  # MAP-04
    ("post", "/api/v1/admin/resources"),  # RES-03
    ("patch", "/api/v1/admin/resources/{resource_id}"),  # RES-04
    ("delete", "/api/v1/admin/resources/{resource_id}"),  # RES-05
    ("post", "/api/v1/admin/resource-bases"),  # BASE-02
    ("patch", "/api/v1/admin/resource-bases/{base_id}"),  # BASE-03
    ("delete", "/api/v1/admin/resource-bases/{base_id}"),  # BASE-04
    ("patch", "/api/v1/admin/users/{user_id}"),  # USR-03
    ("delete", "/api/v1/admin/users/{user_id}"),  # USR-04
    ("post", "/api/v1/admin/users/{user_id}/password-reset"),  # USR-05
    ("post", "/api/v1/admin/users/{user_id}/unlock"),  # USR-06
]


def test_all_spec_routes_are_registered():
    paths = app.openapi()["paths"]
    have = {(m, p) for p, methods in paths.items() for m in methods if p.startswith("/api/v1")}
    assert set(P3_ROUTES) <= have
    assert len(have) == 46  # 명세 v0.5 전체


@pytest.mark.parametrize("model", [UserUpdateIn, ResourceUpdateIn, BaseUpdateIn])
def test_update_needs_at_least_one_field(model):
    with pytest.raises(ValidationError, match="하나 이상"):
        model()


def test_update_rejects_null_where_only_omission_is_allowed():
    with pytest.raises(ValidationError, match="null로 바꿀 수 없습니다"):
        UserUpdateIn(name=None)
    with pytest.raises(ValidationError, match="null로 바꿀 수 없습니다"):
        ResourceUpdateIn(status=None)
    with pytest.raises(ValidationError, match="null로 바꿀 수 없습니다"):
        BaseUpdateIn(coverage_area_ids=None)
    # null을 받는 칸: 전산 관리자의 관할 · 투입 산불 풀기 · 기지 위치 미상
    assert UserUpdateIn(role="ADMIN", admin_area_id=None).model_fields_set == {
        "role",
        "admin_area_id",
    }
    assert ResourceUpdateIn(deployed_incident_id=None).model_dump(exclude_unset=True) == {
        "deployed_incident_id": None
    }
    assert BaseUpdateIn(geometry=None).model_dump(exclude_unset=True) == {"geometry": None}


def test_update_rejects_unknown_fields_and_keeps_only_sent_ones():
    with pytest.raises(ValidationError):
        UserUpdateIn(login_id="x")  # 아이디는 바꾸지 않는다
    assert UserUpdateIn(organization="의성군").model_dump(exclude_unset=True) == {
        "organization": "의성군"
    }


def test_resource_create_defaults_and_string_ids():
    body = ResourceCreateIn(kind="HELICOPTER", name="시험 헬기", base_id="31")
    assert body.base_id == 31 and body.status == "AVAILABLE" and body.attrs == {}
    assert body.deployed_incident_id is None
    with pytest.raises(ValidationError):
        ResourceCreateIn(kind="DRONE", name="x", base_id=1)


@pytest.mark.parametrize("value", [-1, "8000", True, None])
def test_capacity_must_be_a_non_negative_number(value):
    with pytest.raises(ApiError) as e:
        check_attrs({"capacity_l": value})
    assert e.value.status == 422 and e.value.fields[0]["field"] == "attrs.capacity_l"


def test_capacity_is_optional():
    check_attrs({})
    check_attrs({"model": "KA-32", "capacity_l": 0})
    check_attrs({"capacity_l": 3000.5})
