import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, Document, Source
from oss_server.curation import services
from oss_server.curation.models import Event, Proposal, Review


@pytest.fixture
def doc(db: None) -> Document:
    c = Collection.objects.create(slug="cur", name="cur")
    s = Source.objects.create(collection=c, name="s", connector="filesystem")
    return Document.objects.create(
        id="d" * 32,
        collection=c,
        source=s,
        uri="file:///a",
        content_hash="h",
        title="A",
        status=Document.Status.INDEXED,
        tags=[
            {
                "iri": "https://p/themes#flood",
                "label": "Flood",
                "kind": "concept",
                "stage": "tag",
                "confidence": 0.9,
                "spans": [{"start": 0, "end": 5}],
                "pack_id": "p",
                "pack_version": "1",
            },
            {
                "iri": "https://semascope.dev/ontology/base#Person/odysseus",
                "label": "Odysseus",
                "kind": "entity",
                "stage": "ner",
                "confidence": 0.7,
                "spans": [],
            },
            {
                "iri": "https://p/themes#law",
                "label": "Law",
                "kind": "concept",
                "stage": "tag",
                "confidence": 0.75,
                "spans": [],
                "pack_id": "p",
                "pack_version": "1",
            },
        ],
        fields={
            "shacl_violations": [
                {"path": "https://x#confidence", "message": "too high", "severity": "Violation"}
            ]
        },
    )


def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "apply_proposal", lambda p: None)


def test_queue_reviews_opens_items_for_low_confidence_and_violations(doc: Document) -> None:
    opened = services.queue_reviews(doc)
    kinds = sorted(Review.objects.filter(document=doc).values_list("kind", flat=True))
    assert opened == 2 and kinds == ["tag", "violation"], (
        "NER without candidates is skipped; a weak tag is reviewed"
    )
    assert services.queue_reviews(doc) == 0, "re-running opens nothing new"


def test_propose_is_idempotent_and_emits_events(
    doc: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet(monkeypatch)
    a = services.propose(
        doc.collection,
        Proposal.Kind.ACCEPT_TAG,
        "https://p/themes#flood",
        params={"label": "Flood"},
        document=doc,
    )
    b = services.propose(
        doc.collection,
        Proposal.Kind.ACCEPT_TAG,
        "https://p/themes#flood",
        params={"label": "Flood"},
        document=doc,
    )
    assert a.pk == b.pk and len(a.external_ref) == 64
    events = list(Event.objects.filter(kind="proposal.accept_tag"))
    assert len(events) == 1 and events[0].payload["external_ref"] == a.external_ref


def test_review_decisions_through_the_api(
    doc: Document, client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet(monkeypatch)
    user = User.objects.create_user("cur", password="v" * 16, is_staff=True)
    auth = {"Authorization": f"Bearer {issue_token(user, 't')}"}
    services.queue_reviews(doc)
    items = client.get("/api/curation/reviews?collection=cur", headers=auth).json()
    assert len(items) == 2
    summary = client.get("/api/curation/reviews/summary", headers=auth).json()
    assert summary == {"tag": 1, "violation": 1}
    tag_item = next(i for i in items if i["kind"] == "tag")
    r = client.post(
        f"/api/curation/reviews/{tag_item['id']}/decision",
        data={"decision": "reject", "note": "not this"},
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert Proposal.objects.filter(kind="reject_tag", target=tag_item["target"]).exists()
    viol = next(i for i in items if i["kind"] == "violation")
    r = client.post(
        f"/api/curation/reviews/{viol['id']}/decision",
        data={"decision": "accept"},
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 200 and Proposal.objects.filter(kind="dismiss_violation").exists()
    assert client.get("/api/curation/reviews/summary", headers=auth).json() == {}
    events = client.get("/api/curation/events?since_id=0", headers=auth).json()
    assert [e["kind"] for e in events] == ["proposal.reject_tag", "proposal.dismiss_violation"]
    later = client.get(f"/api/curation/events?since_id={events[0]['id']}", headers=auth).json()
    assert len(later) == 1


def test_annotations_and_direct_tag_decisions(
    doc: Document, client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet(monkeypatch)
    user = User.objects.create_user("cur2", password="v" * 16, is_staff=True)
    auth = {"Authorization": f"Bearer {issue_token(user, 't')}"}
    r = client.post(
        f"/api/curation/documents/{doc.id}/annotations",
        data={"text": "check page 3", "tags": ["todo"]},
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 201 and r.json()["author"] == "cur2"
    assert (
        len(client.get(f"/api/curation/documents/{doc.id}/annotations", headers=auth).json()) == 1
    )
    r = client.post(
        f"/api/curation/documents/{doc.id}/tags",
        data={"iri": "https://p/themes#creation", "decision": "accept", "label": "Creation"},
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 200 and r.json()["kind"] == "add_tag"
    tags = client.get(f"/api/curation/documents/{doc.id}/tags", headers=auth).json()
    assert {t["iri"] for t in tags} >= {"https://p/themes#flood"}
    plain_user = User.objects.create_user("plain", password="v" * 16)
    plain = {"Authorization": f"Bearer {issue_token(plain_user, 't')}"}
    assert client.get("/api/curation/webhooks", headers=plain).status_code == 403
