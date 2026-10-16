"""The explanation of a tag through the API: provenance, kept evidence and decision history."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server import services as server_services
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, Document, Source
from oss_server.curation import services
from oss_server.curation.models import Proposal, Review, ReviewDecision
from oss_server.knowledge.models import Concept, Pack

FLOOD = "https://p/themes#flood"
ODY = "https://semascope.dev/ontology/base#Person/odysseus"
MYTH_ODY = "https://myth/odysseus"


class FakeSolr:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def add(self, collection: str, docs: list[dict[str, Any]], **_: Any) -> None:
        self.updates.extend(docs)


class FakeGraph:
    def execute(self, cypher: str, **params: Any) -> None:
        return None


@pytest.fixture
def solr(monkeypatch: pytest.MonkeyPatch) -> FakeSolr:
    fake = FakeSolr()
    monkeypatch.setattr(server_services, "solr_client", lambda: fake)
    monkeypatch.setattr(server_services, "graph_client", lambda: FakeGraph())
    return fake


@pytest.fixture
def doc(db: None) -> Document:
    c = Collection.objects.create(slug="ex", name="ex")
    s = Source.objects.create(collection=c, name="s", connector="filesystem")
    return Document.objects.create(
        id="f" * 32,
        collection=c,
        source=s,
        uri="file:///x",
        content_hash="h",
        status=Document.Status.INDEXED,
        text="Flood. Odysseus.",
        pages=["Flood. Odysseus."],
        tags=[
            {
                "iri": FLOOD,
                "label": "Flood",
                "kind": "concept",
                "stage": "tag",
                "stage_version": "2",
                "confidence": 0.75,
                "matched_label": "flood",
                "label_type": "hidden",
                "lang": "en",
                "spans": [{"start": 0, "end": 5, "page": 1, "matched": "flood"}],
                "pack_id": "p",
                "pack_version": "4",
            },
            {
                "iri": ODY,
                "label": "Odysseus",
                "kind": "entity",
                "stage": "ner",
                "stage_version": "3",
                "confidence": 0.7,
                "model": "spacy:en_core_web_sm",
                "spans": [{"start": 7, "end": 15, "page": 1}],
            },
        ],
    )


def _auth(name: str = "cur") -> dict[str, str]:
    user = User.objects.create_user(name, is_staff=True)
    return {"Authorization": f"Bearer {issue_token(user, 't')}"}


@pytest.mark.django_db
def test_a_rejected_tag_keeps_its_evidence_and_pack_version(doc: Document, solr: FakeSolr) -> None:
    p = services.propose(
        doc.collection,
        Proposal.Kind.REJECT_TAG,
        FLOOD,
        params={"label": "Flood", "kind": "concept"},
        document=doc,
    )
    assert (p.pack_id, p.pack_version) == ("p", "4"), "from the tag being decided"
    evidence = p.params["tag"]
    assert evidence["confidence"] == 0.75 and evidence["stage"] == "tag"
    assert evidence["spans"] == [{"start": 0, "end": 5, "page": 1, "matched": "flood"}]
    assert evidence["span_count"] == 1 and evidence["label_type"] == "hidden"
    # the snapshot is evidence: it does not change the decision's reference
    assert p.external_ref == Proposal.make_ref(
        "ex", "reject_tag", FLOOD, {"label": "Flood", "kind": "concept"}, doc.id
    )
    # repeating the decision is still a no-op
    again = services.propose(
        doc.collection,
        Proposal.Kind.REJECT_TAG,
        FLOOD,
        params={"label": "Flood", "kind": "concept"},
        document=doc,
    )
    assert again.pk == p.pk


@pytest.mark.django_db
def test_a_link_records_the_individuals_pack_and_survives_in_the_view(
    doc: Document, solr: FakeSolr, client: Client
) -> None:
    pack = Pack.objects.create(
        pack_id="myth",
        version="3",
        title="Myth",
        base_iri="https://myth/",
        status=Pack.Status.ACTIVE,
        manifest_hash="m",
        graph_iri="urn:myth",
    )
    Concept.objects.create(pack=pack, iri=MYTH_ODY, kind="individual", pref_label="Odysseus")
    review = Review.objects.create(
        collection=doc.collection, document=doc, kind="link", target=ODY, label="Odysseus"
    )
    auth = _auth()
    r = client.post(
        f"/api/curation/reviews/{review.pk}/decision",
        data={"decision": "accept", "link_to": MYTH_ODY, "label": "Odysseus"},
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 200, r.content
    p = Proposal.objects.get(kind=Proposal.Kind.LINK_ENTITY)
    assert (p.pack_id, p.pack_version) == ("myth", "3")
    assert p.params["tag"]["model"] == "spacy:en_core_web_sm"
    doc.refresh_from_db()
    assert any(t["iri"] == ODY for t in doc.tags), "the machine's NER tag is kept as found"
    assert MYTH_ODY in solr.updates[-1]["entity_iris_ss"]["set"]
    tags = client.get(f"/api/curation/documents/{doc.id}/tags", headers=auth).json()
    linked = next(t for t in tags if t["iri"] == MYTH_ODY)
    assert linked["pack_version"] == "3" and linked["stage"] == "curated"
    assert linked["linked_from"]["iri"] == ODY
    assert linked["linked_from"]["model"] == "spacy:en_core_web_sm"
    assert linked["spans"] == [{"start": 7, "end": 15, "page": 1, "matched": None}]
    assert all(t["iri"] != ODY for t in tags)
    # the history of the individual includes the link decision made on the NER entity
    h = client.get(
        f"/api/curation/documents/{doc.id}/tags/history",
        {"iri": MYTH_ODY},
        headers=auth,
    ).json()
    assert [x["kind"] for x in h["proposals"]] == ["link_entity"]
    assert h["reviews"][0]["decisions"][0]["proposal_id"] == p.pk


@pytest.mark.django_db
def test_tags_api_returns_spans_provenance_and_flags_rejections(
    doc: Document, solr: FakeSolr, client: Client
) -> None:
    auth = _auth()
    r = client.post(
        f"/api/curation/documents/{doc.id}/tags",
        data={"iri": FLOOD, "decision": "reject", "label": "Flood", "kind": "concept"},
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 200, r.content
    tags = {
        t["iri"]: t
        for t in client.get(f"/api/curation/documents/{doc.id}/tags", headers=auth).json()
    }
    flood = tags[FLOOD]
    assert flood["rejected"] is True and flood["curated"]["decision"] == "reject"
    assert flood["spans"] == [{"start": 0, "end": 5, "page": 1, "matched": "flood"}]
    assert flood["span_count"] == 1
    for key, value in {
        "stage": "tag",
        "stage_version": "2",
        "matched_label": "flood",
        "label_type": "hidden",
        "lang": "en",
        "pack_id": "p",
        "pack_version": "4",
    }.items():
        assert flood[key] == value, key
    ody = tags[ODY]
    assert ody["rejected"] is False and ody["model"] == "spacy:en_core_web_sm"


@pytest.mark.django_db
def test_every_decision_on_a_review_is_kept(doc: Document, solr: FakeSolr, client: Client) -> None:
    review = Review.objects.create(
        collection=doc.collection, document=doc, kind="tag", target=FLOOD, label="Flood"
    )
    auth = _auth()
    url = f"/api/curation/reviews/{review.pk}/decision"
    for decision, note in (("defer", "ask the archivist"), ("reject", "not about floods")):
        r = client.post(
            url,
            data={"decision": decision, "note": note},
            content_type="application/json",
            headers=auth,
        )
        assert r.status_code == 200, r.content
    r = client.post(url, data={"decision": "accept"}, content_type="application/json", headers=auth)
    assert r.status_code == 409, "a decided review is not decided again"
    review.refresh_from_db()
    assert review.status == Review.Status.REJECTED and review.note == "not about floods"
    history = list(ReviewDecision.objects.filter(review=review))
    assert [(x.decision, x.note) for x in history] == [
        ("defer", "ask the archivist"),
        ("reject", "not about floods"),
    ]
    assert history[0].proposal is None and history[1].proposal is not None
    assert all(x.decided_by is not None for x in history)
    h = client.get(
        f"/api/curation/documents/{doc.id}/tags/history", {"iri": FLOOD}, headers=auth
    ).json()
    assert h["curated"] == {"decision": "reject"}
    assert [x["kind"] for x in h["proposals"]] == ["reject_tag"]
    assert h["proposals"][0]["params"]["tag"]["confidence"] == 0.75
    assert [x["decision"] for x in h["reviews"][0]["decisions"]] == ["defer", "reject"]


@pytest.mark.django_db
def test_a_direct_decision_settles_open_and_deferred_reviews(
    doc: Document, solr: FakeSolr, client: Client
) -> None:
    review = Review.objects.create(
        collection=doc.collection,
        document=doc,
        kind="tag",
        target=FLOOD,
        label="Flood",
        status=Review.Status.DEFERRED,
    )
    auth = _auth()
    r = client.post(
        f"/api/curation/documents/{doc.id}/tags",
        data={"iri": FLOOD, "decision": "accept", "label": "Flood", "reason": "checked"},
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 200, r.content
    review.refresh_from_db()
    assert review.status == Review.Status.ACCEPTED and review.decided_by is not None
    (row,) = ReviewDecision.objects.filter(review=review)
    assert row.decision == "accept" and row.note == "checked" and row.proposal_id == r.json()["id"]


@pytest.mark.django_db
def test_the_migration_gives_decided_reviews_a_history(doc: Document) -> None:
    import importlib
    from datetime import UTC, datetime

    from django.apps import apps

    migration = importlib.import_module("oss_server.curation.migrations.0003_review_decisions")
    when = datetime(2026, 10, 1, tzinfo=UTC)
    user = User.objects.create_user("old")
    decided = Review.objects.create(
        collection=doc.collection,
        document=doc,
        kind="tag",
        target=FLOOD,
        status=Review.Status.REJECTED,
        decided_by=user,
        decided_at=when,
        note="before 1.0",
    )
    Review.objects.create(collection=doc.collection, document=doc, kind="tag", target="open")
    migration.backfill(apps, None)
    (row,) = ReviewDecision.objects.all()
    assert (row.review_id, row.decision, row.note, row.decided_by_id, row.decided_at) == (
        decided.pk,
        "reject",
        "before 1.0",
        user.pk,
        when,
    )
