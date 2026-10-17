"""The connectors of the ingestion work (WS-7) through the API: created, masked, refused."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server.api.auth import issue_token
from oss_server.catalogue.models import CollectionRole


@pytest.fixture
def editor(db: None) -> dict[str, str]:
    user = User.objects.create_user("editor", password="x" * 16)
    CollectionRole.objects.create(user=user, collection=None, role="editor")
    return {"Authorization": f"Bearer {issue_token(user, 'test')}"}


def _post(client: Client, path: str, auth: dict[str, str], body: dict[str, Any]) -> Any:
    return client.post(path, data=body, content_type="application/json", headers=auth)


@pytest.mark.parametrize("connector", ["warc", "hypothesis", "pst"])
def test_the_new_connectors_are_known(
    client: Client, editor: dict[str, str], connector: str
) -> None:
    _post(client, "/api/collections", editor, {"slug": "ing"})
    settings = {"group": "g"} if connector == "hypothesis" else {"path": "/import/x"}
    r = _post(
        client,
        "/api/collections/ing/sources",
        editor,
        {"name": connector, "connector": connector, "settings": settings},
    )
    assert r.status_code == 201, r.content


def test_a_hypothesis_token_is_never_read_back(client: Client, editor: dict[str, str]) -> None:
    _post(client, "/api/collections", editor, {"slug": "ann"})
    body = {
        "name": "notes",
        "connector": "hypothesis",
        "settings": {"group": "g", "token": "6879-secret-token"},
    }
    created = _post(client, "/api/collections/ann/sources", editor, body)
    assert created.status_code == 201 and "secret" not in created.content.decode()
    listed = client.get("/api/collections/ann/sources", headers=editor)
    assert listed.json()[0]["settings"] == {"group": "g", "token": "********"}
