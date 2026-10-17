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


def test_a_changed_sidecar_alone_queues_its_document_again(
    db: None, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.catalogue.models import Collection, Document, Source
    from oss_server.pipeline import tasks

    queued: list[tuple[Any, ...]] = []
    monkeypatch.setattr(tasks.process_document, "delay", lambda *a, **k: queued.append(a))
    folder = tmp_path / "side"
    folder.mkdir()
    (folder / "plan.pdf").write_bytes(b"%PDF-1.4 plan")
    meta = folder / "plan.pdf.meta.json"
    meta.write_text('{"owner": "Ops"}')
    c = Collection.objects.create(slug="side", name="side")
    s = Source.objects.create(
        collection=c, name="f", connector="filesystem", settings={"path": str(folder)}
    )

    def run() -> dict[str, int]:
        queued.clear()
        counts = tasks.discover_source(s.pk)
        Source.objects.filter(pk=s.pk).update(status=Source.Status.IDLE)
        return counts

    assert run()["queued"] == 1  # plan.pdf only: its sidecar is not a document
    doc_id, item = queued[0]
    # as if processed: the stored fields name the sidecar it was processed with
    Document.objects.filter(pk=doc_id).update(
        status=Document.Status.INDEXED, fields=item["metadata"]["fields"]
    )
    assert run()["queued"] == 0
    meta.write_text('{"owner": "Finance"}')
    counts = run()
    assert counts["queued"] == 1 and queued[0][1]["metadata"]["fields"]["x_owner_s"] == "Finance"
