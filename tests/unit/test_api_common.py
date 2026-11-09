from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.envelope import Envelope, add_request_id, ok
from app.api.errors import ApiError, install_error_handlers
from app.api.pagination import cut, decode_cursor, encode_cursor
from app.schemas.common import DateTimeOut, Id, In


class Item(BaseModel):
    id: Id
    at: DateTimeOut


class Body(In):
    name: str


def make_app() -> FastAPI:
    app = FastAPI()
    add_request_id(app)
    install_error_handlers(app, prefix="")  # 시험용 앱은 모든 경로에

    @app.get("/item", response_model=Envelope[Item])
    def item(request: Request):
        return ok(request, Item(id=7, at=datetime(2025, 3, 22, 2, 25, tzinfo=UTC)))

    @app.get("/fail")
    def fail():
        raise ApiError(409, "INCIDENT_ENDED", "종료된 산불입니다.")

    @app.post("/body")
    def body(b: Body):
        return {"ok": b.name}

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret detail")

    return app


client = TestClient(make_app(), raise_server_exceptions=False)


def test_ok_envelope_ids_are_strings_and_times_are_kst():
    r = client.get("/item")
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == {"id": "7", "at": "2025-03-22T11:25:00+09:00"}
    assert body["meta"]["request_id"] == r.headers["x-request-id"]


def test_api_error_shape():
    r = client.get("/fail")
    assert r.status_code == 409
    assert r.json()["error"] == {
        "code": "INCIDENT_ENDED",
        "message": "종료된 산불입니다.",
        "fields": [],
    }


def test_validation_error_lists_fields_and_rejects_unknown_fields():
    r = client.post("/body", json={"name": "a", "extra": 1})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert [f["field"] for f in err["fields"]] == ["extra"]


def test_broken_json_is_400():
    r = client.post("/body", content="{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "BAD_REQUEST"


def test_unknown_route_and_unexpected_error():
    assert client.get("/nope").json()["error"]["code"] == "NOT_FOUND"
    r = client.get("/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in r.text


def test_cursor_roundtrip_and_bad_cursor():
    c = encode_cursor("2025-03-22T11:25:00+09:00", 100)
    assert decode_cursor(c, 2) == ["2025-03-22T11:25:00+09:00", 100]
    assert decode_cursor(None, 2) is None
    with pytest.raises(ApiError) as e:
        decode_cursor("not-a-cursor", 2)
    assert e.value.code == "INVALID_CURSOR"
    with pytest.raises(ApiError):
        decode_cursor(c, 1)


def test_cut_returns_next_cursor_only_when_more():
    items, nxt, more = cut([1, 2, 3], 2, lambda x: (x,))
    assert (items, more) == ([1, 2], True)
    assert decode_cursor(nxt, 1) == [2]
    assert cut([1, 2], 2, lambda x: (x,)) == ([1, 2], None, False)
