"""Per-collection read access: what a user may read follows their roles and the default role."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server.api.access import readable_collections, role_for
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole, Document, SavedSearch, Source
from oss_server.curation.models import Annotation, Event, Review


@pytest.fixture(autouse=True)
def _fast_passwords(settings: Any) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


class RecordingSolr:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.calls.append({"collection": collection, **params})
        return {"response": {"numFound": 0, "docs": []}, "facet_counts": {}}

    def count(self, collection: str, query: str = "*:*") -> int:
        return 0


@pytest.fixture
def solr(monkeypatch: pytest.MonkeyPatch) -> RecordingSolr:
    from oss_server import services
    from oss_server.api import root, searchplus

    fake = RecordingSolr()
    for module in (services, root, searchplus):
        monkeypatch.setattr(module, "solr_client", lambda: fake)
    return fake


@pytest.fixture
def world(db: None) -> dict[str, Any]:
    a = Collection.objects.create(slug="a", name="a")
    b = Collection.objects.create(slug="b", name="b")
    Collection.objects.create(slug="c", name="c")
    alice = User.objects.create_user("alice")  # reader on a
    bob = User.objects.create_user("bob")  # curator on b
    CollectionRole.objects.create(user=alice, collection=a, role="reader")
    CollectionRole.objects.create(user=bob, collection=b, role="curator")
    src_a = Source.objects.create(collection=a, name="s", connector="filesystem")
    src_b = Source.objects.create(collection=b, name="s", connector="filesystem")
    doc_a = Document.objects.create(
        id="a" * 32, collection=a, source=src_a, uri="u:a", content_hash="h"
    )
    doc_b = Document.objects.create(
        id="b" * 32, collection=b, source=src_b, uri="u:b", content_hash="h"
    )
    Review.objects.create(collection=a, document=doc_a, kind="tag", target="x")
    Review.objects.create(collection=b, document=doc_b, kind="tag", target="x")
    Event.objects.create(kind="proposal.created", collection="a")
    Event.objects.create(kind="proposal.created", collection="b")
    Event.objects.create(kind="alert.saved_search", collection="")
    return {"alice": alice, "bob": bob, "doc_a": doc_a, "doc_b": doc_b}


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user, 't')}"}


def test_default_reader_keeps_reading_open(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "reader")
    alice = world["alice"]
    assert role_for(alice, "b") == "reader" and readable_collections(alice) is None
    nobody = User.objects.create_user("nobody")
    assert readable_collections(nobody) is None


def test_default_none_limits_reading_to_granted_collections(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    alice, bob = world["alice"], world["bob"]
    assert role_for(alice, "a") == "reader" and role_for(alice, "b") == ""
    assert readable_collections(alice) == {"a"} and readable_collections(bob) == {"b"}
    staff = User.objects.create_user("staff", is_staff=True)
    assert readable_collections(staff) is None
    globe = User.objects.create_user("globe")
    CollectionRole.objects.create(user=globe, collection=None, role="reader")
    assert readable_collections(globe) is None and role_for(globe, "c") == "reader"
    assert readable_collections(User.objects.create_user("nobody")) == set()


def test_unknown_default_role_fails_closed(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "raeder")
    assert role_for(world["alice"], "b") == ""


def test_lists_are_filtered_to_readable_collections(
    world: dict[str, Any], client: Client, monkeypatch: pytest.MonkeyPatch, solr: RecordingSolr
) -> None:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    auth = _auth(world["alice"])
    assert [c["slug"] for c in client.get("/api/collections", headers=auth).json()] == ["a"]
    reviews = client.get("/api/curation/reviews", headers=auth).json()
    assert {r["collection"] for r in reviews} == {"a"}
    assert client.get("/api/curation/reviews/summary", headers=auth).json() == {"tag": 1}
    events = client.get("/api/curation/events", headers=auth).json()
    assert {e["collection"] for e in events} == {"a"}  # no collection-less alert events
    stats = client.get("/api/stats", headers=auth).json()
    assert stats["collections"] == 1 and stats["sources"] == 1
    assert client.get(f"/api/documents/{world['doc_b'].id}", headers=auth).status_code == 403
    assert client.get(f"/api/documents/{world['doc_a'].id}", headers=auth).status_code == 200


def test_search_without_a_collection_covers_only_readable_ones(
    world: dict[str, Any], client: Client, monkeypatch: pytest.MonkeyPatch, solr: RecordingSolr
) -> None:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    auth = _auth(world["alice"])
    r = client.post("/api/search", data={"q": "x"}, content_type="application/json", headers=auth)
    assert r.status_code == 200
    assert 'collection_s:("a")' in solr.calls[-1]["fq"]
    r = client.post(
        "/api/search",
        data={"q": "x", "collection": "b"},
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 403
    nobody = _auth(User.objects.create_user("nobody"))
    client.post("/api/search", data={"q": "x"}, content_type="application/json", headers=nobody)
    assert "-*:*" in solr.calls[-1]["fq"]
    client.post(
        "/api/search/export.csv", data={"q": "x"}, content_type="application/json", headers=auth
    )
    assert 'collection_s:("a")' in solr.calls[-1]["fq"]
    client.get("/api/knowledge/places", headers=auth)  # no pack places: answers before Solr
    staff = _auth(User.objects.create_user("staff", is_staff=True))
    client.post("/api/search", data={"q": "x"}, content_type="application/json", headers=staff)
    assert not any(str(f).startswith("collection_s:(") for f in solr.calls[-1]["fq"])


def test_semantic_chunks_are_filtered_too(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import search as search_mod
    from oss_server.api.schemas import SearchIn

    class Embedder:
        model_name = "m"

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1] * 384]  # the bundled model's dimension

    solr = RecordingSolr()
    monkeypatch.setattr(search_mod, "query_embedder", lambda: Embedder())
    search_mod.search(solr, "documents", SearchIn(q="flood", mode="semantic"), ["a"])  # type: ignore[arg-type]
    chunk_call = next(c for c in solr.calls if c["collection"] == "chunks")
    assert 'collection_s:("a")' in chunk_call["fq"]


def test_graph_reads_pass_the_readable_collections(
    world: dict[str, Any], client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import graph

    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    seen: list[tuple[str, dict[str, Any]]] = []

    def raw(cypher: str, **params: Any) -> list[Any]:
        seen.append((cypher, params))
        return []

    class G:
        def run(self, cypher: str, **params: Any) -> list[Any]:
            seen.append((cypher, params))
            return []

    monkeypatch.setattr(graph, "_raw", raw)
    monkeypatch.setattr(graph, "graph_client", lambda: G())
    auth = _auth(world["alice"])
    client.get("/api/graph/neighbours?id=Entity:x", headers=auth)
    client.get("/api/graph/path?a=Entity:x&b=Entity:y", headers=auth)
    client.get("/api/graph/shared", headers=auth)
    assert len(seen) == 3
    for cypher, params in seen:
        assert "$allowed" in cypher and params["allowed"] == ["a"]
    assert client.get("/api/graph/shared?collection=b", headers=auth).status_code == 403


def test_annotations_need_curator_and_authors_may_delete_their_own(
    world: dict[str, Any], client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.curation import services

    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    monkeypatch.setattr(services, "apply_proposal", lambda *a, **k: None)
    alice, bob = world["alice"], world["bob"]
    doc_a, doc_b = world["doc_a"], world["doc_b"]
    url = f"/api/curation/documents/{doc_a.id}/annotations"
    r = client.post(url, data={"text": "hi"}, content_type="application/json", headers=_auth(alice))
    assert r.status_code == 403
    url_b = f"/api/curation/documents/{doc_b.id}/annotations"
    r = client.post(url_b, data={"text": "hi"}, content_type="application/json", headers=_auth(bob))
    assert r.status_code == 201
    note = r.json()["id"]
    # bob is demoted to reader on b: he may still delete his own note, not someone else's
    CollectionRole.objects.filter(user=bob).update(role="reader")
    other = Annotation.objects.create(document=doc_b, text="not bob's")
    assert (
        client.delete(f"/api/curation/annotations/{other.id}", headers=_auth(bob)).status_code
        == 403
    )
    assert client.delete(f"/api/curation/annotations/{note}", headers=_auth(bob)).status_code == 204
    # alice cannot read b at all, so not even an author's delete of a note there would pass
    stray = Annotation.objects.create(document=doc_b, text="alice's", author=alice)
    assert (
        client.delete(f"/api/curation/annotations/{stray.id}", headers=_auth(alice)).status_code
        == 403
    )


def test_alerts_only_see_what_their_owner_may_read(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import search as search_mod
    from oss_server.pipeline import tasks

    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    seen: list[Any] = []

    def fake_search(client: Any, name: str, req: Any, allowed: Any = None) -> Any:
        seen.append((req.collection, allowed))
        from oss_server.api.schemas import SearchOut

        return SearchOut(total=0, page=1, page_size=5, took_ms=0, hits=[], facets={}, histogram=[])

    monkeypatch.setattr(search_mod, "search", fake_search)
    monkeypatch.setattr(tasks, "solr_client", lambda: RecordingSolr())
    alice = world["alice"]
    SavedSearch.objects.create(user=alice, name="all", query={"q": "x"}, alert=True)
    SavedSearch.objects.create(
        user=alice, name="b", query={"q": "x", "collection": "b"}, alert=True
    )
    tasks.check_alerts()
    assert seen == [(None, ["a"])]  # the alert on b is skipped: alice cannot read b


def test_saving_a_search_on_an_unreadable_collection_is_refused(
    world: dict[str, Any], client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    r = client.post(
        "/api/search/saved",
        data={"name": "n", "query": {"q": "x", "collection": "b"}},
        content_type="application/json",
        headers=_auth(world["alice"]),
    )
    assert r.status_code == 403


def test_source_settings_never_echo_credentials(world: dict[str, Any], client: Client) -> None:
    staff = User.objects.create_user("staff", is_staff=True)
    Source.objects.create(
        collection=Collection.objects.get(slug="c"),
        name="bucket",
        connector="s3",
        settings={
            "bucket": "x",
            "access_key": "AKIA",
            "secret_key": "s3cr3t",
            "nested": {"token": "t"},
        },
    )
    out = client.get("/api/collections/c/sources", headers=_auth(staff)).json()[0]["settings"]
    masked = "********"
    assert out["secret_key"] == masked and out["nested"]["token"] == masked
    assert out["bucket"] == "x"


def test_sources_outside_the_roots_are_refused(
    world: dict[str, Any], client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = tmp_path / "import"
    roots.mkdir()
    (roots / "escape").symlink_to("/etc")
    monkeypatch.setenv("SEMASCOPE_SOURCE_ROOTS", str(roots))
    monkeypatch.setenv("SEMASCOPE_DATA_DIR", str(tmp_path / "data"))
    auth = _auth(User.objects.create_user("staff", is_staff=True))

    def create(connector: str, path: str) -> int:
        return client.post(
            "/api/collections/c/sources",
            data={
                "name": f"{connector}-{len(path)}",
                "connector": connector,
                "settings": {"path": path},
            },
            content_type="application/json",
            headers=auth,
        ).status_code

    for connector in ("filesystem", "mail", "archive", "table", "rdf"):
        assert create(connector, "/etc") == 422
        assert create(connector, str(roots / ".." / "..")) == 422
        assert create(connector, str(roots / "escape")) == 422
    assert create("filesystem", str(roots)) == 201
    assert create("upload", str(tmp_path / "data" / "uploads" / "a")) == 422  # another collection
    assert create("upload", str(tmp_path / "data" / "uploads" / "c")) == 201
    r = client.post(
        "/api/collections/c/sources",
        data={"name": "w", "connector": "web", "settings": {"urls": ["file:///etc/passwd"]}},
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 422


def test_a_source_moved_outside_the_roots_fails_at_run_time(
    world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.pipeline.tasks import discover_source

    monkeypatch.setenv("SEMASCOPE_SOURCE_ROOTS", str(tmp_path / "import"))
    s = Source.objects.create(
        collection=Collection.objects.get(slug="c"),
        name="old",
        connector="filesystem",
        settings={"path": "/etc"},
    )
    assert discover_source(s.id) == {"error": 1}
    s.refresh_from_db()
    assert s.status == Source.Status.FAILED and s.last_error.startswith("refused:")


def test_a_decided_review_cannot_be_decided_again(
    world: dict[str, Any], client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.curation import services

    monkeypatch.setattr(services, "apply_proposal", lambda *a, **k: None)
    auth = _auth(world["bob"])  # curator on b
    review = Review.objects.get(collection__slug="b")
    url = f"/api/curation/reviews/{review.id}/decision"

    def decide(decision: str) -> int:
        return client.post(
            url, data={"decision": decision}, content_type="application/json", headers=auth
        ).status_code

    assert decide("defer") == 200
    assert decide("reject") == 200  # a deferred item can still be decided
    assert decide("accept") == 409
    assert decide("defer") == 409


def test_deleting_a_document_removes_its_passages_too(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.pipeline import tasks

    calls: list[tuple[str, Any]] = []

    class S:
        def delete_by_id(self, collection: str, ids: list[str]) -> None:
            calls.append((collection, ids))

        def delete_by_query(self, collection: str, query: str) -> None:
            calls.append((collection, query))

    class G:
        def execute(self, *a: Any, **k: Any) -> None:
            return None

    from oss_server import services

    monkeypatch.setattr(tasks, "solr_client", lambda: S())
    monkeypatch.setattr(services, "graph_client", lambda: G())
    doc = world["doc_a"]
    tasks.delete_document(doc.id)
    assert ("chunks", f'document_id:"{doc.id}"') in calls
    doc.refresh_from_db()
    assert doc.status == Document.Status.DELETED and doc.text == ""
