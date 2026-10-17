"""Every API route, called by every kind of user, answers with the status its rule expects.

The table below holds one rule per route. A route without a rule fails
`test_every_route_has_a_rule`, so a new endpoint cannot ship without someone
deciding who may call it. The deployment runs with the default
SEMASCOPE_DEFAULT_ROLE (none), so collection roles alone decide who reads what.

Users: anonymous; `other` (reader on collection b only); `reader`, `curator` and
`editor` on collection a; `admin` (staff). Requirements:

- "public": anyone, signed in or not
- "auth": any signed-in user (lists are filtered to readable collections)
- ("reader" | "curator" | "editor" | "admin", "a"): that role on collection a
- ("editor", None): a global editor (packs, new collections)
- "staff": Django staff only
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from oss_server.api.auth import issue_token
from oss_server.api.root import api
from oss_server.catalogue.models import (
    Collection,
    CollectionRole,
    Document,
    SavedSearch,
    Source,
)
from oss_server.curation.models import (
    Annotation,
    BulkTagJob,
    Event,
    Proposal,
    Review,
    Webhook,
)
from oss_server.knowledge.models import Concept, Pack, PackDelta, PackJob

USERS = ("anonymous", "other", "reader", "curator", "editor", "admin")
ROLE_ORDER = ("reader", "curator", "editor", "admin")
ROLES_ON_A = {"reader": "reader", "curator": "curator", "editor": "editor", "admin": "admin"}
CONCEPT_IRI = "https://example.org/p#c"
IRI_Q = quote(CONCEPT_IRI, safe="")

Requirement = str | tuple[str, str | None]


@dataclass
class Rule:
    need: Requirement
    ok: int
    query: str = ""
    json: Any = None
    files: bool = False


TABLE: dict[tuple[str, str], Rule] = {
    # collections and sources
    ("GET", "/api/collections"): Rule("auth", 200),
    ("POST", "/api/collections"): Rule(("editor", None), 201, json={"slug": "new"}),
    ("GET", "/api/collections/{slug}"): Rule(("reader", "a"), 200),
    ("DELETE", "/api/collections/{slug}"): Rule(("admin", "a"), 204),
    ("POST", "/api/collections/{slug}/reindex"): Rule(("editor", "a"), 202),
    ("GET", "/api/collections/{slug}/sources"): Rule(("reader", "a"), 200),
    ("POST", "/api/collections/{slug}/sources"): Rule(("editor", "a"), 201, json="source"),
    ("POST", "/api/sources/{source_id}/run"): Rule(("editor", "a"), 202),
    ("DELETE", "/api/sources/{source_id}"): Rule(("editor", "a"), 204),
    ("POST", "/api/collections/{slug}/upload"): Rule(("editor", "a"), 202, files=True),
    # documents
    ("GET", "/api/documents/{doc_id}"): Rule(("reader", "a"), 200),
    ("GET", "/api/documents/{doc_id}/text"): Rule(("reader", "a"), 200),
    ("GET", "/api/documents/{doc_id}/preview/{page}"): Rule(("reader", "a"), 200),
    ("POST", "/api/documents/{doc_id}/reindex"): Rule(("editor", "a"), 202),
    # search
    ("POST", "/api/search"): Rule(("reader", "a"), 200, json={"q": "x", "collection": "a"}),
    ("POST", "/api/search/tag-query"): Rule("auth", 200, json={"q": "x"}),
    ("GET", "/api/suggest"): Rule(("reader", "a"), 200, query="q=x&collection=a"),
    ("GET", "/api/stats"): Rule("auth", 200),
    ("GET", "/api/search/saved"): Rule("auth", 200),
    ("POST", "/api/search/saved"): Rule(
        ("reader", "a"), 201, json={"name": "n", "query": {"q": "x", "collection": "a"}}
    ),
    ("DELETE", "/api/search/saved/{saved_id}"): Rule("auth", 204),
    ("POST", "/api/search/list"): Rule(
        ("reader", "a"), 200, json={"names": ["x"], "collection": "a"}
    ),
    ("POST", "/api/search/export.csv"): Rule(
        ("reader", "a"), 200, json={"q": "x", "collection": "a"}
    ),
    ("POST", "/api/search/terms"): Rule(("reader", "a"), 200, json={"q": "x", "collection": "a"}),
    # accounts
    ("GET", "/api/auth/providers"): Rule("public", 200),
    ("GET", "/api/auth/csrf"): Rule("public", 200),
    ("POST", "/api/auth/login"): Rule(
        "public", 200, json={"username": "login", "password": "l" * 16}
    ),
    ("POST", "/api/auth/logout"): Rule("auth", 204),
    ("GET", "/api/auth/me"): Rule("auth", 200),
    ("GET", "/api/ui/config"): Rule("auth", 200),
    # knowledge
    ("GET", "/api/knowledge/packs"): Rule("auth", 200),
    ("POST", "/api/knowledge/packs/import"): Rule(("editor", None), 422, json={"path": "/nowhere"}),
    ("GET", "/api/knowledge/packs/{pack_id}"): Rule("auth", 200),
    ("POST", "/api/knowledge/packs/{pack_id}/activate"): Rule(
        ("editor", None), 202, query="version=1"
    ),
    ("POST", "/api/knowledge/packs/import-list"): Rule(
        ("editor", None), 202, json={"pack_id": "names", "text": "Alpha | A\nBeta"}
    ),
    ("GET", "/api/knowledge/pack-jobs"): Rule(("editor", None), 200),
    ("GET", "/api/knowledge/pack-jobs/{pack_job_id}"): Rule(("editor", None), 200),
    ("POST", "/api/knowledge/packs/diff"): Rule(("editor", None), 422, json={"path": "/nowhere"}),
    ("GET", "/api/knowledge/packs/{pack_id}/diff"): Rule(("editor", None), 200, query="version=0"),
    ("DELETE", "/api/knowledge/packs/{pack_id}/versions/{version}"): Rule(("admin", None), 200),
    ("GET", "/api/knowledge/packs/{pack_id}/tree"): Rule("auth", 200),
    ("GET", "/api/knowledge/packs/{pack_id}/concepts"): Rule("auth", 200),
    ("GET", "/api/knowledge/concepts"): Rule("auth", 200, query=f"iri={IRI_Q}"),
    ("GET", "/api/knowledge/concepts/documents"): Rule(
        ("reader", "a"), 200, query=f"iri={IRI_Q}&collection=a"
    ),
    ("GET", "/api/knowledge/places"): Rule(("reader", "a"), 200, query="collection=a"),
    ("POST", "/api/knowledge/packs/{pack_id}/sparql"): Rule(
        "staff", 200, json={"query": "SELECT * WHERE { ?s ?p ?o }"}
    ),
    ("GET", "/api/knowledge/packs/{pack_id}/deltas"): Rule("auth", 200),
    ("POST", "/api/knowledge/packs/{pack_id}/deltas"): Rule(
        ("curator", "a"),
        201,
        json={"collection": "a", "kind": "note", "params": {"iri": CONCEPT_IRI, "text": "t"}},
    ),
    ("GET", "/api/knowledge/packs/{pack_id}/deltas/export"): Rule("auth", 200),
    ("POST", "/api/knowledge/packs/{pack_id}/deltas/export"): Rule(("editor", None), 200),
    # reconciliation service (OpenRefine): the active packs' vocabulary, public by design
    ("GET", "/api/reconcile"): Rule("public", 200),
    ("POST", "/api/reconcile"): Rule("public", 200, json={"queries": {}}),
    ("GET", "/api/reconcile/preview"): Rule("public", 200, query=f"iri={IRI_Q}"),
    # graph
    ("GET", "/api/graph/neighbours"): Rule(
        ("reader", "a"), 200, query="id=Document:{doc_id}&collection=a"
    ),
    ("GET", "/api/graph/path"): Rule("auth", 200, query="a=Document:{doc_id}&b=Entity:x"),
    ("GET", "/api/graph/shared"): Rule(("reader", "a"), 200, query="collection=a"),
    ("POST", "/api/graph/cypher"): Rule("staff", 200, json={"query": "MATCH (n) RETURN n"}),
    # curation
    ("GET", "/api/curation/reviews"): Rule(("reader", "a"), 200, query="collection=a"),
    ("GET", "/api/curation/reviews/summary"): Rule(("reader", "a"), 200, query="collection=a"),
    ("POST", "/api/curation/reviews/{review_id}/decision"): Rule(
        ("curator", "a"), 200, json={"decision": "defer"}
    ),
    ("GET", "/api/curation/documents/{doc_id}/tags"): Rule(("reader", "a"), 200),
    ("GET", "/api/curation/documents/{doc_id}/tags/history"): Rule(
        ("reader", "a"), 200, query=f"iri={IRI_Q}"
    ),
    ("POST", "/api/curation/documents/{doc_id}/tags"): Rule(
        ("curator", "a"), 200, json={"iri": CONCEPT_IRI, "decision": "accept"}
    ),
    ("GET", "/api/curation/documents/{doc_id}/annotations"): Rule(("reader", "a"), 200),
    ("POST", "/api/curation/documents/{doc_id}/annotations"): Rule(
        ("curator", "a"), 201, json={"text": "note"}
    ),
    ("DELETE", "/api/curation/annotations/{annotation_id}"): Rule(("curator", "a"), 204),
    ("POST", "/api/curation/bulk-tags"): Rule(
        ("curator", "a"),
        202,
        json={"collection": "a", "q": "x", "iri": CONCEPT_IRI, "decision": "accept"},
    ),
    ("GET", "/api/curation/bulk-tags"): Rule(("reader", "a"), 200, query="collection=a"),
    ("GET", "/api/curation/bulk-tags/{bulk_job_id}"): Rule(("reader", "a"), 200),
    ("GET", "/api/curation/proposals"): Rule(("reader", "a"), 200, query="collection=a"),
    ("GET", "/api/curation/events"): Rule(("reader", "a"), 200, query="collection=a"),
    ("GET", "/api/curation/webhooks"): Rule("staff", 200),
    ("POST", "/api/curation/webhooks"): Rule("staff", 201, json={"url": "https://example.org/h"}),
    ("DELETE", "/api/curation/webhooks/{webhook_id}"): Rule("staff", 204),
    # views of documents and searches (api/views.py)
    ("GET", "/api/documents/{doc_id}/original"): Rule(("reader", "a"), 200),
    ("GET", "/api/documents/{doc_id}/access"): Rule(("reader", "a"), 200),
    ("GET", "/api/documents/{doc_id}/matches"): Rule(("reader", "a"), 200, query="q=some"),
    ("POST", "/api/views/table/stats"): Rule(
        ("reader", "a"), 200, json={"q": "x", "collection": "a"}
    ),
    ("POST", "/api/views/entities"): Rule(("reader", "a"), 200, json={"q": "x", "collection": "a"}),
    ("GET", "/api/views/alerts"): Rule("auth", 200),
    ("POST", "/api/views/graph"): Rule(
        ("reader", "a"), 200, json={"collection": "a", "search": {"q": "x"}}
    ),
    ("POST", "/api/views/graph/edge"): Rule(
        ("reader", "a"),
        200,
        json={"a": "Entity:x", "b": f"Concept:{CONCEPT_IRI}", "collection": "a"},
    ),
    # feeds: the token routes are the caller's own; feeds are read as their owner
    ("GET", "/api/feeds/token"): Rule("auth", 200),
    ("POST", "/api/feeds/token"): Rule("auth", 201),
    ("DELETE", "/api/feeds/token"): Rule("auth", 204),
    ("GET", "/api/feeds/saved/{saved_id}"): Rule("auth", 200),
    ("GET", "/api/feeds/search"): Rule(("reader", "a"), 200, query="q=x&collection=a"),
    ("GET", "/api/feeds/alerts"): Rule("auth", 200),
    # ADR-0012 capabilities (api/extras.py)
    ("GET", "/api/documents/{doc_id}/similar"): Rule(("reader", "a"), 200, query="mode=text"),
    ("GET", "/api/export/documents/{doc_id}"): Rule(("reader", "a"), 200),
    ("POST", "/api/export/search"): Rule(
        ("reader", "a"), 200, query="format=jsonld", json={"q": "x", "collection": "a"}
    ),
    ("GET", "/api/annotations/w3c"): Rule(("reader", "a"), 200, query="collection=a"),
    ("POST", "/api/annotations/w3c/import"): Rule(
        ("curator", "a"), 200, query="collection=a", json={"type": "AnnotationPage", "items": []}
    ),
    # the description holds no data and browsers fetch it without cookies; searching needs a session
    ("GET", "/api/opensearch.xml"): Rule("public", 200),
    ("GET", "/api/opensearch/suggest"): Rule("auth", 200, query="q=x"),
    ("GET", "/api/analytics/overview"): Rule(("reader", "a"), 200, query="collection=a"),
    # ADR-0012, the rest of WS-12: analysis views, table and graph exports, synonym tooling,
    # ranking explain, the Web Annotation Protocol's per-annotation operations
    ("POST", "/api/analytics/clusters"): Rule(
        ("reader", "a"), 200, json={"search": {"q": "x", "collection": "a"}}
    ),
    ("POST", "/api/analytics/compare"): Rule(
        ("reader", "a"), 200, json={"search": {"q": "x", "collection": "a"}}
    ),
    ("POST", "/api/analytics/wordtree"): Rule(
        ("reader", "a"), 200, json={"search": {"q": "x", "collection": "a"}, "phrase": "some"}
    ),
    ("GET", "/api/analytics/diff"): Rule(("reader", "a"), 200, query="a={doc_id}&b={doc_id}"),
    ("POST", "/api/export/documents"): Rule(
        ("reader", "a"), 200, query="format=jsonl", json={"q": "x", "collection": "a"}
    ),
    ("POST", "/api/export/entities"): Rule(
        ("reader", "a"), 200, query="format=parquet", json={"q": "x", "collection": "a"}
    ),
    ("GET", "/api/export/graph"): Rule(("reader", "a"), 200, query="collection=a&format=nquads"),
    # the active packs' labels are every signed-in user's; drafts are filtered to readable ones
    ("GET", "/api/synonyms/expand"): Rule("auth", 200, query="q=c"),
    ("GET", "/api/synonyms/suggest"): Rule(("reader", "a"), 200, query=f"iri={IRI_Q}&collection=a"),
    ("POST", "/api/explain"): Rule(
        ("reader", "a"), 200, json={"search": {"q": "x", "collection": "a"}, "doc_id": "d" * 32}
    ),
    ("POST", "/api/annotations/w3c"): Rule(
        ("curator", "a"),
        201,
        query="collection=a",
        json={"type": "Annotation", "bodyValue": "n", "target": "urn:semascope:doc:" + "d" * 32},
    ),
    ("GET", "/api/annotations/w3c/{annotation_id}"): Rule(("reader", "a"), 200),
    # the matrix sends no If-Match: past the role check, the protocol asks for one (428)
    ("PUT", "/api/annotations/w3c/{annotation_id}"): Rule(
        ("curator", "a"), 428, json={"type": "Annotation", "bodyValue": "changed"}
    ),
    ("DELETE", "/api/annotations/w3c/{annotation_id}"): Rule(("curator", "a"), 428),
}


def registered_routes() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for prefix, router in api._routers:  # noqa: SLF001 - the test walks the real route table
        for path, view in router.path_operations.items():
            for op in view.operations:
                for method in op.methods:
                    out.append((method, "/api" + prefix + path))
    return sorted(set(out))


def expected(rule: Rule, user: str) -> int:
    need = rule.need
    if need == "public":
        return rule.ok
    if user == "anonymous":
        return 401
    if need == "auth":
        return rule.ok
    if need == "staff":
        return rule.ok if user == "admin" else 403
    assert isinstance(need, tuple)
    role, scope = need
    if user == "admin":
        return rule.ok
    if scope is None or user == "other":
        return 403  # no one here holds a global role, and `other` has none on a
    have = ROLES_ON_A[user]
    return rule.ok if ROLE_ORDER.index(have) >= ROLE_ORDER.index(role) else 403


class FakeSolr:
    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        return {"response": {"numFound": 0, "docs": []}, "facet_counts": {}}

    def count(self, collection: str, query: str = "*:*") -> int:
        return 0

    def add(self, *args: Any, **kwargs: Any) -> None:
        return None

    def delete_by_query(self, *args: Any, **kwargs: Any) -> None:
        return None

    def commit(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeGraph:
    def run(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []


class FakeResult:
    id = "task-1"


def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Solr, Neo4j, Celery or pack store: every external call answers empty."""

    from oss_server import services
    from oss_server.api import graph, root, searchplus
    from oss_server.curation import services as curation_services
    from oss_server.knowledge import store
    from oss_server.pipeline import tasks

    solr = FakeSolr()
    for module in (services, root, searchplus):
        monkeypatch.setattr(module, "solr_client", lambda: solr)
    monkeypatch.setattr(services, "graph_client", lambda: FakeGraph())
    monkeypatch.setattr(graph, "graph_client", lambda: FakeGraph())
    monkeypatch.setattr(graph, "_raw", lambda *a, **k: [])
    monkeypatch.setattr(services, "sync_dictionary", lambda *a, **k: 0)
    monkeypatch.setattr(store, "query", lambda *a, **k: [])
    monkeypatch.setattr(store, "drop_graph", lambda *a, **k: None)
    monkeypatch.setattr(curation_services, "apply_proposal", lambda *a, **k: None)
    for name in (
        "reindex_collection",
        "discover_source",
        "process_document",
        "reindex_document",
        "purge_documents",
        "purge_area",
    ):
        monkeypatch.setattr(getattr(tasks, name), "delay", lambda *a, **k: FakeResult())
    from oss_server.curation import tasks as curation_tasks
    from oss_server.knowledge import tasks as knowledge_tasks

    monkeypatch.setattr(knowledge_tasks.run_pack_job, "delay", lambda *a, **k: FakeResult())
    monkeypatch.setattr(knowledge_tasks.retag_label, "apply_async", lambda *a, **k: FakeResult())
    monkeypatch.setattr(curation_tasks.run_bulk_tag_job, "delay", lambda *a, **k: FakeResult())


def _world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.delenv("SEMASCOPE_DEFAULT_ROLE", raising=False)  # the default: none
    monkeypatch.setenv("SEMASCOPE_DATA_DIR", str(tmp_path / "data"))
    imports = tmp_path / "import"
    imports.mkdir()
    monkeypatch.setenv("SEMASCOPE_SOURCE_ROOTS", str(imports))
    _offline(monkeypatch)

    a = Collection.objects.create(slug="a", name="a")
    b = Collection.objects.create(slug="b", name="b")
    users: dict[str, User] = {
        "other": User.objects.create_user("other"),
        "reader": User.objects.create_user("reader"),
        "curator": User.objects.create_user("curator"),
        "editor": User.objects.create_user("editor"),
        "admin": User.objects.create_user("admin", is_staff=True),
    }
    User.objects.create_user("login", password="l" * 16)
    CollectionRole.objects.create(user=users["other"], collection=b, role="reader")
    for name in ("reader", "curator", "editor"):
        CollectionRole.objects.create(user=users[name], collection=a, role=name)

    source = Source.objects.create(
        collection=a, name="files", connector="filesystem", settings={"path": str(imports)}
    )
    previews = tmp_path / "previews"
    previews.mkdir()
    (previews / "1.webp").write_bytes(b"RIFF0000WEBP")
    blob = tmp_path / "data" / "blobs" / "ha" / "hash.txt"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"some text")
    doc = Document.objects.create(
        id="d" * 32,
        collection=a,
        source=source,
        uri="file:///import/x.txt",
        content_hash="h",
        title="X",
        text="some text",
        status=Document.Status.INDEXED,
        media_type="text/plain",
        blob_path=str(blob),
        fields={"preview_dir": str(previews)},
    )
    pack = Pack.objects.create(
        pack_id="p",
        version="1",
        title="P",
        base_iri="https://example.org/p#",
        status=Pack.Status.ACTIVE,
        manifest_hash="m",
        graph_iri="urn:p",
    )
    Concept.objects.create(pack=pack, iri=CONCEPT_IRI, kind="concept", pref_label="C")
    old = Pack.objects.create(
        pack_id="p",
        version="0",
        title="P",
        base_iri="https://example.org/p#",
        status=Pack.Status.RETIRED,
        manifest_hash="m0",
        graph_iri="urn:p0",
    )
    Concept.objects.create(pack=old, iri=CONCEPT_IRI, kind="concept", pref_label="Old C")
    PackDelta.objects.create(
        pack_id="p", pack_version="1", collection=a, kind="note", params={"iri": CONCEPT_IRI}
    )
    review = Review.objects.create(collection=a, document=doc, kind="tag", target=CONCEPT_IRI)
    annotation = Annotation.objects.create(document=doc, text="by someone else")
    Proposal.objects.create(
        collection=a,
        document=doc,
        kind="add_note",
        target="t",
        external_ref="r",
    )
    Event.objects.create(kind="proposal.created", collection="a")
    webhook = Webhook.objects.create(url="https://example.org/hook")
    pack_job = PackJob.objects.create(kind="import", pack_id="p", version="1", status="done")
    bulk_job = BulkTagJob.objects.create(collection=a, iri=CONCEPT_IRI, decision="accept")
    return {
        "users": users,
        "ids": {
            "slug": "a",
            "source_id": source.id,
            "doc_id": doc.id,
            "page": 1,
            "pack_id": "p",
            "version": "0",
            "review_id": review.id,
            "annotation_id": annotation.id,
            "webhook_id": webhook.id,
            "pack_job_id": pack_job.id,
            "bulk_job_id": bulk_job.id,
        },
        "imports": imports,
    }


def _call(client: Client, method: str, path: str, rule: Rule, world: dict[str, Any], user: str):  # type: ignore[no-untyped-def]
    ids = dict(world["ids"])
    headers: dict[str, str] = {}
    if user != "anonymous":
        u = world["users"][user]
        headers["Authorization"] = f"Bearer {issue_token(u, 'matrix')}"
        ids["saved_id"] = SavedSearch.objects.create(user=u, name="mine", query={"q": "x"}).id
    else:
        ids["saved_id"] = 0
    url = path.format(**ids)
    if rule.query:
        url += "?" + rule.query.format(**ids)
    body: Any = rule.json
    if body == "source":
        body = {
            "name": "more",
            "connector": "filesystem",
            "settings": {"path": str(world["imports"])},
        }
    send: Callable[..., Any] = getattr(client, method.lower())
    if rule.files:
        return send(url, data={"files": SimpleUploadedFile("n.txt", b"hello")}, headers=headers)
    if body is not None:
        return send(url, data=body, content_type="application/json", headers=headers)
    return send(url, headers=headers)


ROUTES = registered_routes()


@pytest.fixture(autouse=True)
def _fast_passwords(settings: Any) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def test_every_route_has_a_rule() -> None:
    missing = [route for route in ROUTES if route not in TABLE]
    assert not missing, f"routes without an authorisation rule: {missing}"


@pytest.mark.django_db
@pytest.mark.parametrize("user", USERS)
@pytest.mark.parametrize("route", [r for r in ROUTES if r in TABLE], ids="{0[0]} {0[1]}".format)
def test_route_answers_each_user_as_its_rule_says(
    route: tuple[str, str],
    user: str,
    client: Client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    method, path = route
    rule = TABLE[route]
    world = _world(tmp_path, monkeypatch)
    resp = _call(client, method, path, rule, world, user)
    want = expected(rule, user)
    assert resp.status_code == want, (
        f"{method} {path} as {user}: {resp.status_code}, expected {want}: {resp.content[:300]!r}"
    )
