"""The Web Annotation Protocol's per-annotation operations (ANN-07): create in the container,
read with an ETag, replace and delete with If-Match; curators, authors and readers."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server import services as server_services
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole, Document, Source
from oss_server.curation.models import Annotation, Proposal

DOC = "a" * 32
TEXT = "Tell me, O Muse, of that ingenious hero who travelled far and wide."


class NoStores:
    def select(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"response": {"numFound": 0, "docs": []}}

    def add(self, *args: Any, **kwargs: Any) -> None:
        return None

    def execute(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture
def world(db: None, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    monkeypatch.setattr(server_services, "solr_client", lambda: NoStores())
    monkeypatch.setattr(server_services, "graph_client", lambda: NoStores())
    a = Collection.objects.create(slug="a", name="a")
    b = Collection.objects.create(slug="b", name="b")
    users = {
        name: User.objects.create_user(name) for name in ("reader", "curator", "author", "other")
    }
    CollectionRole.objects.create(user=users["reader"], collection=a, role="reader")
    CollectionRole.objects.create(user=users["author"], collection=a, role="reader")
    CollectionRole.objects.create(user=users["curator"], collection=a, role="curator")
    CollectionRole.objects.create(user=users["other"], collection=b, role="curator")
    s = Source.objects.create(collection=a, name="s", connector="filesystem")
    sb = Source.objects.create(collection=b, name="s", connector="filesystem")
    Document.objects.create(
        id=DOC,
        collection=a,
        source=s,
        uri="https://example.org/odyssey",
        content_hash="h",
        text=TEXT,
    )
    Document.objects.create(
        id="b" * 32, collection=b, source=sb, uri="https://example.org/odyssey", content_hash="h"
    )
    return users


def _h(user: User, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user, 't')}", **extra}


def annotation(text: str, target: Any) -> dict[str, Any]:
    return {
        "@context": "http://www.w3.org/ns/anno.jsonld",
        "type": "Annotation",
        "body": [
            {"type": "TextualBody", "value": text, "purpose": "commenting"},
            {"type": "TextualBody", "value": "hero", "purpose": "tagging"},
        ],
        "target": target,
    }


def create(client: Client, user: User, body: dict[str, Any], query: str = "") -> Any:
    return client.post(
        "/api/annotations/w3c" + query,
        body,
        "application/ld+json",
        headers=_h(user),
    )


def test_create_read_replace_delete(client: Client, world: dict[str, User]) -> None:
    target = {
        "source": f"urn:semascope:doc:{DOC}",
        "selector": {"type": "TextQuoteSelector", "exact": "ingenious hero", "prefix": "that "},
    }
    resp = create(client, world["curator"], annotation("the hero", target))
    assert resp.status_code == 201, resp.content
    url = resp["Location"]
    assert url.startswith("http://testserver/api/annotations/w3c/")
    assert resp["Content-Type"].startswith("application/ld+json")
    body = resp.json()
    assert body["id"] == url and body["canonical"].startswith("urn:semascope:annotation:")
    pk = int(url.rsplit("/", 1)[1])
    note = Annotation.objects.get(pk=pk)
    assert note.text == "the hero" and note.tags == ["hero"]
    assert TEXT[note.span["start"] : note.span["end"]] == "ingenious hero"
    assert Proposal.objects.filter(target=f"annotation:{pk}", kind="add_note").count() == 1

    path = f"/api/annotations/w3c/{pk}"
    got = client.get(path, headers=_h(world["reader"]))
    assert got.status_code == 200 and got["ETag"] == resp["ETag"]
    assert 'ldp#Resource>; rel="type"' in got["Link"]
    assert (
        client.get(path, headers=_h(world["reader"], **{"If-None-Match": got["ETag"]})).status_code
        == 304
    )

    change = annotation("a wily hero", {"source": f"urn:semascope:doc:{DOC}"})
    assert (
        client.put(path, change, "application/ld+json", headers=_h(world["curator"])).status_code
        == 428
    )
    stale = client.put(
        path, change, "application/ld+json", headers=_h(world["curator"], **{"If-Match": '"old"'})
    )
    assert stale.status_code == 412
    done = client.put(
        path,
        change,
        "application/ld+json",
        headers=_h(world["curator"], **{"If-Match": got["ETag"]}),
    )
    assert done.status_code == 200, done.content
    assert done["ETag"] != got["ETag"] and done.json()["body"][0]["value"] == "a wily hero"
    note.refresh_from_db()
    assert note.text == "a wily hero" and note.span is None
    assert Proposal.objects.filter(target=f"annotation:{pk}", kind="add_note").count() == 2

    assert client.delete(path, headers=_h(world["reader"], **{"If-Match": "*"})).status_code == 403
    gone = client.delete(path, headers=_h(world["curator"], **{"If-Match": done["ETag"]}))
    assert gone.status_code == 204 and not Annotation.objects.filter(pk=pk).exists()


def test_targets_by_uri_need_a_collection_when_ambiguous(
    client: Client, world: dict[str, User]
) -> None:
    body = annotation("note", "https://example.org/odyssey")
    # the curator reads only a, so the URI is not ambiguous for them
    assert create(client, world["curator"], body).status_code == 201
    staff = User.objects.create_user("staff", is_staff=True)
    assert create(client, staff, body).status_code == 409
    assert create(client, staff, body, "?collection=b").status_code == 201
    assert create(client, world["curator"], body, "?collection=b").status_code == 403
    nowhere = annotation("note", "https://example.org/unknown")
    assert create(client, world["curator"], nowhere).status_code == 404
    assert create(client, world["curator"], {"type": "Annotation"}).status_code == 400


def test_readers_may_not_create_and_authors_edit_their_own(
    client: Client, world: dict[str, User]
) -> None:
    target = {"source": f"urn:semascope:doc:{DOC}"}
    assert create(client, world["reader"], annotation("x", target)).status_code == 403
    assert create(client, world["other"], annotation("x", target)).status_code == 403
    own = Annotation.objects.create(document_id=DOC, author=world["author"], text="mine")
    path = f"/api/annotations/w3c/{own.pk}"
    tag = client.get(path, headers=_h(world["author"]))["ETag"]
    resp = client.put(
        path,
        annotation("still mine", target),
        "application/ld+json",
        headers=_h(world["author"], **{"If-Match": tag}),
    )
    assert resp.status_code == 200, resp.content
    moved = client.put(
        path,
        annotation("elsewhere", {"source": "urn:semascope:doc:" + "b" * 32}),
        "application/ld+json",
        headers=_h(world["author"], **{"If-Match": resp["ETag"]}),
    )
    assert moved.status_code == 409
    assert client.get(path, headers=_h(world["other"])).status_code == 403


def test_the_container_says_what_it_is(client: Client, world: dict[str, User]) -> None:
    resp = client.get("/api/annotations/w3c?collection=a", headers=_h(world["reader"]))
    assert resp.status_code == 200
    assert 'ldp#BasicContainer>; rel="type"' in resp["Link"] and "POST" in resp["Allow"]


def test_entity_tags_survive_a_compressing_proxy() -> None:
    from oss_server.api.annotations_ldp import tags_of

    assert tags_of('"abc-zstd"') == {'"abc"'}
    assert tags_of('W/"abc-gzip", "def"') == {'"abc"', '"def"'}
    assert tags_of('"my-tag"') == {'"my-tag"'}
