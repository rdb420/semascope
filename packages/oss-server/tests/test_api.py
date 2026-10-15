from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server.api.auth import issue_token
from oss_server.api.schemas import SearchIn
from oss_server.api.search import build_params, parse_response
from oss_server.catalogue.models import document_id


@pytest.fixture
def token(db: None) -> str:
    user = User.objects.create_user("tester", password="x" * 16)
    return issue_token(user, "test")


def test_healthz(client: Client) -> None:
    assert client.get("/healthz").json()["status"] == "ok"


def test_token_required(client: Client, db: None) -> None:
    assert client.get("/api/collections").status_code == 401


def test_collection_and_source_lifecycle(client: Client, token: str, tmp_path: Path) -> None:
    auth = {"Authorization": f"Bearer {token}"}
    r = client.post(
        "/api/collections", data={"slug": "demo"}, content_type="application/json", headers=auth
    )
    assert r.status_code == 201, r.content
    assert (
        client.post(
            "/api/collections", data={"slug": "demo"}, content_type="application/json", headers=auth
        ).status_code
        == 409
    )
    r = client.post(
        "/api/collections/demo/sources",
        data={"name": "files", "connector": "filesystem", "settings": {"path": str(tmp_path)}},
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 201, r.content
    bad = client.post(
        "/api/collections/demo/sources",
        data={"name": "x", "connector": "nope"},
        content_type="application/json",
        headers=auth,
    )
    assert bad.status_code == 422
    listed = client.get("/api/collections", headers=auth).json()
    assert listed[0]["slug"] == "demo"


def test_document_ids_are_deterministic() -> None:
    assert document_id("c", "file:///a") == document_id("c", "file:///a")
    assert document_id("c", "file:///a") != document_id("d", "file:///a")


def test_search_params_and_parse() -> None:
    req = SearchIn(
        q="flood",
        collection="demo",
        filters={"language_s": ["en"], "nope": ["x"]},
        page=2,
        page_size=5,
    )
    params = build_params(req)
    assert params["start"] == 5 and params["rows"] == 5
    assert 'collection_s:"demo"' in params["fq"]
    assert any(f.startswith("{!tag=language_s}") for f in params["fq"])
    assert not any("nope" in f for f in params["fq"])
    body = {
        "responseHeader": {"QTime": 7},
        "response": {
            "numFound": 1,
            "docs": [
                {
                    "id": "a",
                    "title_txt": "T",
                    "uri_s": "file:///t",
                    "collection_s": "demo",
                    "source_id_s": "1",
                }
            ],
        },
        "highlighting": {"a": {"text_txt": ["a <mark>flood</mark>"]}},
        "facet_counts": {"facet_fields": {"language_s": ["en", 1]}},
    }
    out = parse_response(req, body)
    assert out.total == 1 and out.hits[0].snippets == ["a <mark>flood</mark>"]
    assert out.facets["language_s"][0].value == "en"


def test_session_login_and_me(client: Client, db: None) -> None:
    User.objects.create_user("ui", password="y" * 16)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/csrf").json()["csrftoken"]
    bad = client.post(
        "/api/auth/login",
        data={"username": "ui", "password": "nope"},
        content_type="application/json",
    )
    assert bad.status_code == 401
    ok = client.post(
        "/api/auth/login",
        data={"username": "ui", "password": "y" * 16},
        content_type="application/json",
    )
    assert ok.status_code == 200 and ok.json()["username"] == "ui"
    assert client.get("/api/auth/me").json()["username"] == "ui"
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
