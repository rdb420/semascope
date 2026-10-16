"""RDF export (ETL-EXP-04, ETL-EXP-05): documents, tags in force, notes and decisions as Turtle
and JSON-LD in the base pack's vocabulary, read-scoped, capped and pseudonymised."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from rdflib import RDF, RDFS, SKOS, Graph, Literal, Namespace, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS

from oss_server import services as server_services
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole, Document, Source
from oss_server.curation import services
from oss_server.curation.models import Annotation, Proposal
from oss_server.exports import ids

BASE = Namespace("https://semascope.dev/ontology/base#")
OA = Namespace("http://www.w3.org/ns/oa#")
FLOOD = "https://p/themes#flood"
GREEK = "https://p/traditions#greek"
ZEUS = "https://p/demo#zeus"
ODYSSEUS = "https://semascope.dev/ontology/base#Person/odysseus"
JOVE = "https://semascope.dev/ontology/base#Person/great jove"
ROOT = Path(__file__).resolve().parents[3]


class FakeSolr:
    """Solr's document hits for a search; the store updates curation makes are dropped."""

    def __init__(self, ids_: list[str] | None = None) -> None:
        self.ids = ids_ or []
        self.calls: list[dict[str, Any]] = []

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.calls.append({"collection": collection, **params})
        start, rows = int(params.get("start", 0)), int(params.get("rows", 10))
        docs = [{"id": i, "uri_s": f"u:{i}"} for i in self.ids[start : start + rows]]
        return {"response": {"numFound": len(self.ids), "docs": docs}, "facet_counts": {}}

    def add(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeGraph:
    def execute(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture
def solr(monkeypatch: pytest.MonkeyPatch) -> FakeSolr:
    fake = FakeSolr()
    monkeypatch.setattr(server_services, "solr_client", lambda: fake)
    monkeypatch.setattr(server_services, "graph_client", lambda: FakeGraph())
    return fake


def _tag(iri: str, label: str, kind: str, stage: str, **extra: Any) -> dict[str, Any]:
    return {"iri": iri, "label": label, "kind": kind, "stage": stage, "confidence": 0.9, **extra}


def make_doc(slug: str = "texts", settings: dict[str, Any] | None = None, n: str = "a") -> Document:
    c, _ = Collection.objects.get_or_create(
        slug=slug, defaults={"name": f"The {slug}", "settings": settings or {}}
    )
    s, _ = Source.objects.get_or_create(collection=c, name="files", connector="filesystem")
    return Document.objects.create(
        id=n * 32,
        collection=c,
        source=s,
        uri=f"file:///import/{slug}/the odyssey {n}.txt",
        content_hash="cafe",
        title="The Odyssey",
        media_type="text/plain",
        language="en",
        text="Sing to me of the man, Muse. Zeus and great Jove watched the flood.",
        pages=["Sing to me of the man, Muse.", "Zeus and great Jove watched the flood."],
        status=Document.Status.INDEXED,
        tags=[
            _tag(
                FLOOD,
                "Flood",
                "concept",
                "tag",
                pack_id="demo",
                pack_version="0.1.0",
                stage_version="3",
                spans=[{"start": 61, "end": 66}],
            ),
            _tag(GREEK, "Greek", "concept", "tag", pack_id="demo", pack_version="0.1.0"),
            _tag(ODYSSEUS, "Odysseus", "entity", "ner", model="spacy:en_core_web_sm"),
            _tag(JOVE, "great Jove", "entity", "ner"),
        ],
    )


def decide(
    doc: Document, decision: str, target: str, user: User | None = None, **params: Any
) -> Proposal:
    return services.propose(
        doc.collection, decision, target, params=params, document=doc, user=user
    )


def turtle(client: Client, doc: Document, auth: dict[str, str]) -> Graph:
    resp = client.get(f"/api/export/documents/{doc.id}", headers=auth)
    assert resp.status_code == 200, resp.content
    assert resp["Content-Type"].startswith("text/turtle")
    return Graph().parse(data=body(resp), format="turtle")


def body(resp: Any) -> str:
    """The text of a streamed response."""

    return b"".join(resp.streaming_content).decode()


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user, 't')}"}


@pytest.fixture
def staff(db: None) -> User:
    return User.objects.create_user("curie", is_staff=True)


@pytest.mark.django_db
def test_document_exports_with_base_vocabulary_tags_in_force_and_decisions(
    client: Client, solr: FakeSolr, staff: User
) -> None:
    doc = make_doc()
    decide(doc, Proposal.Kind.REJECT_TAG, GREEK, staff, label="Greek", kind="concept")
    decide(doc, Proposal.Kind.LINK_ENTITY, JOVE, staff, iri=ZEUS, label="Zeus")
    Annotation.objects.create(
        document=doc, author=staff, text="the flood", span={"start": 61, "end": 66}
    )
    g = turtle(client, doc, _auth(staff))

    d = URIRef(ids.doc_iri(doc.id))
    assert (d, RDF.type, BASE.Document) in g
    assert (d, DCTERMS.title, Literal("The Odyssey")) in g
    assert (d, BASE.contentHash, Literal("cafe")) in g
    # the original URI, made a valid IRI (it has spaces)
    assert (d, DCTERMS.source, URIRef("file:///import/texts/the%20odyssey%20a.txt")) in g
    assert (d, BASE.inCollection, URIRef(ids.collection_iri("texts"))) in g
    assert len(list(g.objects(d, BASE.hasPage))) == 2
    targets = {g.value(t, BASE.tagTarget) for t in g.objects(d, BASE.hasTag)}
    # Greek was rejected: not a tag any more; great Jove was linked to Zeus
    assert URIRef(GREEK) not in targets
    assert URIRef(FLOOD) in targets and URIRef(ZEUS) in targets
    assert (URIRef(FLOOD), RDF.type, SKOS.Concept) in g
    flood = next(
        t for t in g.objects(d, BASE.hasTag) if g.value(t, BASE.tagTarget) == URIRef(FLOOD)
    )
    assert g.value(g.value(flood, BASE.producedByPack), BASE.packVersion) == Literal("0.1.0")
    stage = g.value(flood, BASE.producedByStage)
    assert (stage, RDF.type, BASE.StageRun) in g and g.value(stage, DCTERMS.title) == Literal("tag")
    odysseus = URIRef("urn:semascope:entity:Person/odysseus")
    assert (odysseus, RDF.type, BASE.Person) in g
    assert (odysseus, RDFS.label, Literal("Odysseus")) in g
    zeus_tag = next(
        t for t in g.objects(d, BASE.hasTag) if g.value(t, BASE.tagTarget) == URIRef(ZEUS)
    )
    assert g.value(zeus_tag, URIRef("http://www.w3.org/ns/prov#wasDerivedFrom")) == URIRef(
        "urn:semascope:entity:Person/great%20jove"
    )
    # notes and both decisions are Web Annotations on the document's node
    annos = set(g.subjects(RDF.type, OA.Annotation))
    assert len(annos) == 3
    motivations = {g.value(a, OA.motivatedBy) for a in annos}
    assert motivations == {OA.commenting, OA.assessing, OA.identifying}
    for a in annos:
        assert g.value(g.value(a, OA.hasTarget), OA.hasSource) == d
    note = next(a for a in annos if g.value(a, OA.motivatedBy) == OA.commenting)
    quote = next(
        s
        for s in g.objects(g.value(note, OA.hasTarget), OA.hasSelector)
        if (s, RDF.type, OA.TextQuoteSelector) in g
    )
    assert g.value(quote, OA.exact) == Literal("flood")


@pytest.mark.django_db
def test_turtle_and_json_ld_carry_the_same_graph(
    client: Client, solr: FakeSolr, staff: User
) -> None:
    doc = make_doc()
    decide(doc, Proposal.Kind.ACCEPT_TAG, FLOOD, staff, label="Flood", kind="concept")
    auth = _auth(staff)
    ttl = turtle(client, doc, auth)
    resp = client.get(f"/api/export/documents/{doc.id}?format=jsonld", headers=auth)
    assert resp["Content-Type"].startswith("application/ld+json")
    jsonld = Graph().parse(data=body(resp), format="json-ld")
    assert len(ttl) > 40
    assert isomorphic(ttl, jsonld)


@pytest.mark.django_db
def test_export_answers_the_base_packs_competency_question(
    client: Client, solr: FakeSolr, staff: User
) -> None:
    """CQ-001 of packs/base: documents tagged with a concept, with pack version and stage."""

    doc = make_doc()
    g = turtle(client, doc, _auth(staff))
    query = (ROOT / "packs" / "base" / "queries" / "cq-001.rq").read_text()
    query = query.replace("<https://semascope.dev/example/collection-demo>", "?collection")
    query = query.replace("<https://semascope.dev/example/concept-flood>", f"<{FLOOD}>")
    rows: list[Any] = list(g.query(query))
    assert [(str(r[0]), str(r[1]), str(r[2])) for r in rows] == [
        (ids.doc_iri(doc.id), "0.1.0", "tag")
    ]


@pytest.mark.django_db
def test_pseudonymised_collection_exports_keys_not_names(
    client: Client, solr: FakeSolr, staff: User
) -> None:
    doc = make_doc("private", settings={"pseudonymise": True})
    decide(doc, Proposal.Kind.REJECT_TAG, ODYSSEUS, staff, label="Odysseus", kind="entity")
    resp = client.get(f"/api/export/documents/{doc.id}", headers=_auth(staff))
    text = body(resp)
    assert "Odysseus" not in text and "odysseus" not in text and "great jove" not in text.lower()
    g = Graph().parse(data=text, format="turtle")
    hashed = [
        o for o in g.objects(None, BASE.tagTarget) if str(o).startswith("urn:semascope:entity:h:")
    ]
    assert hashed  # the remaining NER entity, keyed by HMAC like the graph stage
    assert (URIRef(FLOOD), SKOS.prefLabel, Literal("Flood")) in g  # concepts keep their labels


@pytest.mark.django_db
def test_search_export_is_read_scoped_and_capped(
    client: Client, solr: FakeSolr, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    mine = [make_doc("texts", n=c) for c in "abc"]
    other = make_doc("secret", n="f")
    reader = User.objects.create_user("reader")
    CollectionRole.objects.create(user=reader, collection=mine[0].collection, role="reader")
    auth = _auth(reader)
    # a stale index answering with a document of another collection must not widen the export
    solr.ids = [mine[0].id, other.id, mine[1].id, mine[2].id]
    resp = client.post(
        "/api/export/search?limit=2",
        data={"q": "zeus"},
        content_type="application/json",
        headers=auth,
    )
    assert resp.status_code == 200
    g = Graph().parse(data=body(resp), format="turtle")
    exported = set(g.subjects(RDF.type, BASE.Document))
    assert exported == {URIRef(ids.doc_iri(mine[0].id))}  # the limit counts hits; `other` skipped
    assert 'collection_s:("texts")' in solr.calls[0]["fq"]
    denied = client.post(
        "/api/export/search",
        data={"q": "zeus", "collection": "secret"},
        content_type="application/json",
        headers=auth,
    )
    assert denied.status_code == 403
    assert client.get(f"/api/export/documents/{other.id}", headers=auth).status_code == 403
    bad = client.post(
        "/api/export/search?format=xml",
        data={"q": "x"},
        content_type="application/json",
        headers=auth,
    )
    assert bad.status_code == 422


@pytest.mark.django_db
def test_search_export_streams_every_page_up_to_the_limit(
    client: Client, solr: FakeSolr, staff: User
) -> None:
    docs = [make_doc("texts", n=f"{i:x}") for i in range(3)]
    solr.ids = [d.id for d in docs]
    from oss_server.api import exports

    monkey_page = exports.PAGE
    try:
        exports.PAGE = 1  # one hit per search page: three pages
        resp = client.post(
            "/api/export/search?format=jsonld&limit=5",
            data={"q": "*:*"},
            content_type="application/json",
            headers=_auth(staff),
        )
        g = Graph().parse(data=body(resp), format="json-ld")
    finally:
        exports.PAGE = monkey_page
    assert set(g.subjects(RDF.type, BASE.Document)) == {URIRef(ids.doc_iri(d.id)) for d in docs}
