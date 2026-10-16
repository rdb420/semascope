"""A collection's regex lists, law patterns and dollar currency are checked when it is saved."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole
from oss_server.pipeline.tasks import stage_settings


@pytest.fixture
def editor(db: None) -> dict[str, str]:
    user = User.objects.create_user("editor", password="x" * 16)
    CollectionRole.objects.create(user=user, collection=None, role="editor")
    return {"Authorization": f"Bearer {issue_token(user, 'test')}"}


def create(client: Client, auth: dict[str, str], **body: Any) -> Any:
    return client.post("/api/collections", data=body, content_type="application/json", headers=auth)


@pytest.mark.parametrize(
    ("pipeline", "key"),
    [
        ({"regex_lists": {"case": "(a)\\1"}}, "regex_lists"),
        ({"regex_lists": {"Not A Name": "x"}}, "regex_lists"),
        ({"law_patterns": ["(?<=x)y"]}, "law_patterns"),
        ({"dollar_currency": "DOGE"}, "dollar_currency"),
        ({"ocr": "sometimes"}, "ocr"),
    ],
)
def test_bad_extractor_settings_are_refused(
    client: Client, editor: dict[str, str], pipeline: dict[str, Any], key: str
) -> None:
    r = create(client, editor, slug="x1", settings={"pipeline": pipeline})
    assert r.status_code == 422 and key in r.content.decode(), r.content
    assert not Collection.objects.filter(slug="x1").exists()


def test_good_extractor_settings_reach_the_stages(client: Client, editor: dict[str, str]) -> None:
    pipeline = {
        "regex_lists": {"ticket": "TCK-\\d+"},
        "law_patterns": ["Policy P-\\d+"],
        "law_defaults": False,
        "dollar_currency": "AUD",
        "ocr": "later",
    }
    r = create(client, editor, slug="x2", settings={"pipeline": pipeline})
    assert r.status_code == 201, r.content
    settings = stage_settings(Collection.objects.get(slug="x2"))
    assert {k: settings[k] for k in pipeline} == pipeline
