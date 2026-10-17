"""Table and graph exports (ETL-EXP-08, DM-20, ANL-17, ETL-EXP-04): documents and entities as
CSV, JSON Lines and Parquet; N-Quads; a collection's graph as RDF. Streamed, read-scoped,
capped and pseudonymised."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import pyarrow.parquet as pq
import pytest
from django.contrib.auth.models import User
from django.test import Client
from rdflib import RDF, RDFS, SKOS, Dataset, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS

from oss_server import services
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole, Document, Source
from oss_server.exports import ids, tabular
from oss_server.knowledge.models import Concept, Pack

BASE = Namespace("https://semascope.dev/ontology/base#")
FLOOD = "https://p/themes#flood"
WATER = "https://p/themes#water"
ODYSSEUS = "https://semascope.dev/ontology/base#Person/odysseus"


class FakeSolr:
    def __init__(self) -> None:
        self.ids: list[str] = []
        self.calls: list[dict[str, Any]] = []

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.calls.append(params)
        start, rows = int(params.get("start", 0)), int(params.get("rows", 10))
        docs = [{"id": i, "uri_s": i} for i in self.ids[start : start + rows]]
        return {"response": {"numFound": len(self.ids), "docs": docs}, "facet_counts": {}}


@pytest.fixture
def solr(monkeypatch: pytest.MonkeyPatch) -> FakeSolr:
    fake = FakeSolr()
    monkeypatch.setattr(services, "solr_client", lambda: fake)
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    return fake


def _tag(iri: str, label: str, kind: str, stage: str, **extra: Any) -> dict[str, Any]:
    return {"iri": iri, "label": label, "kind": kind, "stage": stage, "confidence": 0.9, **extra}


def make_doc(slug: str, n: str, settings: dict[str, Any] | None = None) -> Document:
    c, _ = Collection.objects.get_or_create(
        slug=slug, defaults={"name": slug, "settings": settings or {}}
    )
    s, _ = Source.objects.get_or_create(collection=c, name="files", connector="filesystem")
    return Document.objects.create(
        id=n * 32,
        collection=c,
        source=s,
        uri=f"file:///import/{slug}/{n}.txt",
        content_hash="cafe",
        title=f"Text {n}",
        media_type="text/plain",
        language="en",
        size=42,
        text="Odysseus watched the flood rise. " * 3,
        status=Document.Status.INDEXED,
        tags=[
            _tag(FLOOD, "Flood", "concept", "tag", pack_id="p", pack_version="1"),
            _tag(ODYSSEUS, "Odysseus", "entity", "ner", spans=[{"start": 0, "end": 8}]),
        ],
    )


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user, 't')}"}


def _body(resp: Any) -> bytes:
    return b"".join(resp.streaming_content)


@pytest.fixture
def reader(db: None) -> User:
    user = User.objects.create_user("reader")
    return user


@pytest.mark.django_db
def test_documents_as_csv_jsonl_and_parquet_read_scoped(
    client: Client, solr: FakeSolr, reader: User
) -> None:
    mine = [make_doc("texts", c) for c in "abc"]
    other = make_doc("secret", "f")
    CollectionRole.objects.create(user=reader, collection=mine[0].collection, role="reader")
    solr.ids = [mine[0].id, other.id, mine[1].id, mine[2].id]  # a stale hit from `secret`
    url = "/api/export/documents?limit=3&format="
    resp = client.post(url + "csv", {"q": "flood"}, "application/json", headers=_auth(reader))
    assert resp.status_code == 200 and resp["Content-Type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(_body(resp).decode())))
    assert [r["id"] for r in rows] == [mine[0].id, mine[1].id]  # the limit counts hits
    assert rows[0]["concepts"] == "Flood" and rows[0]["entities"] == "1"
    assert rows[0]["content_type_group"] and rows[0]["page_url"].endswith(
        f"/documents/{mine[0].id}"
    )
    assert "text" not in rows[0]
    resp = client.post(
        url + "jsonl&text_chars=8", {"q": "flood"}, "application/json", headers=_auth(reader)
    )
    lines = [json.loads(line) for line in _body(resp).decode().splitlines()]
    assert lines[0]["text"] == "Odysseus" and lines[0]["concepts"] == ["Flood"]
    resp = client.post(url + "parquet", {"q": "flood"}, "application/json", headers=_auth(reader))
    table = pq.read_table(io.BytesIO(_body(resp)))
    assert table.num_rows == 2 and table.schema.field("modified_at").type.tz == "UTC"
    assert table.column("concept_iris").to_pylist()[0] == [FLOOD]
    assert 'collection_s:("texts")' in str(solr.calls[0]["fq"])
    bad = client.post(url + "xls", {"q": "x"}, "application/json", headers=_auth(reader))
    assert bad.status_code == 422


@pytest.mark.django_db
def test_entities_one_row_per_tag_and_pseudonymised(
    client: Client, solr: FakeSolr, reader: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = make_doc("texts", "a")
    private = make_doc("private", "b", settings={"pseudonymise": True})
    for d in (plain, private):
        CollectionRole.objects.create(user=reader, collection=d.collection, role="reader")
    solr.ids = [plain.id, private.id]
    resp = client.post(
        "/api/export/entities?format=jsonl", {"q": "x"}, "application/json", headers=_auth(reader)
    )
    rows = [json.loads(line) for line in _body(resp).decode().splitlines()]
    assert len(rows) == 4
    by = {(r["document_id"], r["kind"]): r for r in rows}
    person = by[(plain.id, "entity")]
    assert person["class"] == "Person" and person["label"] == "Odysseus"
    assert person["iri"].startswith("urn:semascope:entity:") and person["mentions"] == 1
    hidden = by[(private.id, "entity")]
    assert hidden["label"] == "" and "odysseus" not in hidden["iri"].lower()
    assert by[(private.id, "concept")]["label"] == "Flood"  # concepts keep their labels
    resp = client.post(
        "/api/export/entities?format=parquet", {"q": "x"}, "application/json", headers=_auth(reader)
    )
    assert pq.read_table(io.BytesIO(_body(resp))).num_rows == 4


def test_parquet_streams_one_row_group_per_batch() -> None:
    rows = [{"document_id": str(i), "mentions": i} for i in range(1200)]
    chunks = list(tabular.parquet_stream(tabular.batched(rows, 500), tabular.ENTITY_COLUMNS))
    assert len(chunks) >= 3  # written as it goes, not held whole
    meta = pq.ParquetFile(io.BytesIO(b"".join(chunks))).metadata
    assert meta.num_rows == 1200 and meta.num_row_groups == 3


@pytest.mark.django_db
def test_document_export_as_nquads_names_the_document_graph(client: Client, solr: FakeSolr) -> None:
    doc = make_doc("texts", "a")
    staff = User.objects.create_user("staff", is_staff=True)
    resp = client.get(f"/api/export/documents/{doc.id}?format=nquads", headers=_auth(staff))
    assert resp.status_code == 200 and resp["Content-Type"] == "application/n-quads"
    ds = Dataset()
    ds.parse(data=_body(resp).decode(), format="nquads")
    graph = ds.graph(URIRef(ids.doc_iri(doc.id)))
    assert (URIRef(ids.doc_iri(doc.id)), RDF.type, BASE.Document) in graph


@pytest.mark.django_db
def test_graph_export_of_a_collection(client: Client, solr: FakeSolr, reader: User) -> None:
    pack = Pack.objects.create(
        pack_id="p",
        version="1",
        title="P",
        base_iri="https://p/",
        status=Pack.Status.ACTIVE,
        manifest_hash="m",
        graph_iri="urn:p",
    )
    Concept.objects.create(
        pack=pack,
        iri=FLOOD,
        kind="concept",
        pref_label="Flood",
        scheme="https://p/themes",
        broader=[WATER],
    )
    docs = [make_doc("texts", c) for c in "ab"]
    make_doc("secret", "f")
    CollectionRole.objects.create(user=reader, collection=docs[0].collection, role="reader")
    resp = client.get("/api/export/graph?collection=texts", headers=_auth(reader))
    assert resp.status_code == 200, resp.content
    g = Graph().parse(data=_body(resp).decode(), format="turtle")
    subjects = set(g.subjects(RDF.type, BASE.Document))
    assert subjects == {URIRef(ids.doc_iri(d.id)) for d in docs}
    assert (URIRef(ids.doc_iri(docs[0].id)), DCTERMS.subject, URIRef(FLOOD)) in g
    assert (URIRef(FLOOD), SKOS.broader, URIRef(WATER)) in g
    assert (URIRef(FLOOD), SKOS.inScheme, URIRef("https://p/themes")) in g
    person = next(g.subjects(RDF.type, BASE.Person))
    assert (person, RDFS.label, Literal("Odysseus")) in g
    assert len(list(g.triples((URIRef(FLOOD), SKOS.prefLabel, None)))) == 1  # described once
    denied = client.get("/api/export/graph?collection=secret", headers=_auth(reader))
    assert denied.status_code == 403
    quads = client.get("/api/export/graph?collection=texts&format=nquads", headers=_auth(reader))
    ds = Dataset()
    ds.parse(data=_body(quads).decode(), format="nquads")
    assert len(ds.graph(URIRef(ids.collection_iri("texts")))) == len(g)
