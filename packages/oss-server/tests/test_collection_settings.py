"""A collection's settings tune its pipeline; they never redirect the workers.

`settings.pipeline` may change sizes, caps, retries and optional work. Service
addresses, credentials, Solr collection names and secrets come from the
deployment only: the API refuses them and the workers ignore them.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_core.config import get_settings
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection
from oss_server.pipeline.overrides import PIPELINE_OVERRIDES
from oss_server.pipeline.tasks import pseudonymise_secret, stage_settings

ELSEWHERE = {
    "solr_url": "http://collector.example.test:8983/solr",
    "solr_user": "someone",
    "solr_documents": "entities",
    "neo4j_uri": "bolt://collector.example.test:7687",
    "neo4j_password": "x",
    "tika_url": "http://collector.example.test:9998",
    "embed_base_url": "http://collector.example.test/v1",
    "ner_base_url": "http://collector.example.test/ner",
    "extract_base_url": "http://collector.example.test/mineru",
    "blob_dir": "/elsewhere",
    "pseudonymise_secret": "chosen-by-the-collection",
}


@pytest.fixture
def editor(db: None) -> dict[str, str]:
    from oss_server.catalogue.models import CollectionRole

    user = User.objects.create_user("editor", password="x" * 16)
    CollectionRole.objects.create(user=user, collection=None, role="editor")
    return {"Authorization": f"Bearer {issue_token(user, 'test')}"}


def create(client: Client, auth: dict[str, str], **body: Any) -> Any:
    return client.post("/api/collections", data=body, content_type="application/json", headers=auth)


@pytest.mark.parametrize("key", sorted(ELSEWHERE))
def test_the_api_refuses_service_settings(client: Client, editor: dict[str, str], key: str) -> None:
    r = create(client, editor, slug="c1", settings={"pipeline": {key: ELSEWHERE[key]}})
    assert r.status_code == 422, r.content
    assert key in r.content.decode()
    assert not Collection.objects.filter(slug="c1").exists()


def test_the_api_accepts_tuning(client: Client, editor: dict[str, str]) -> None:
    tuning = {"chunk_words": 200, "task_retries": 1, "pseudonymise": True, "ocr": False}
    r = create(client, editor, slug="c2", settings={"pipeline": tuning})
    assert r.status_code == 201, r.content
    assert set(tuning) <= PIPELINE_OVERRIDES


def test_the_workers_ignore_service_settings_already_stored(db: None) -> None:
    c = Collection.objects.create(
        slug="old",
        name="Old",
        settings={"pipeline": {**ELSEWHERE, "chunk_words": 99, "pseudonymise": True}},
    )
    s = get_settings()
    out = stage_settings(c)
    assert out["chunk_words"] == 99 and out["pseudonymise"] is True
    assert out["solr_url"] == s.solr_url and out["neo4j_uri"] == s.neo4j_uri
    assert out["tika_url"] == s.tika_url and out["solr_documents"] == "documents"
    assert out["solr_user"] == s.solr_user and out["blob_dir"] != "/elsewhere"
    assert out["embed_base_url"] == s.semascope_embed_base_url
    assert out["neo4j_password"] == s.neo4j_password.get_secret_value()
    assert out["pseudonymise_secret"] == s.semascope_secret_key.get_secret_value()
    assert pseudonymise_secret(c) == s.semascope_secret_key.get_secret_value()
