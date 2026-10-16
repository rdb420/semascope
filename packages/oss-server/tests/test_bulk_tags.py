"""Bulk tagging (ANN-04): one tag decision on every document of a search, as a job, one
proposal per document."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server import services as server_services
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole, Document, Source
from oss_server.curation.models import BulkTagJob, Proposal, Review
from oss_server.knowledge.models import Concept, Pack

FLOOD = "https://p/themes#flood"


class FakeSolr:
    """Documents of a search, served by cursor two at a time; atomic updates recorded."""

    def __init__(self, ids: list[str]) -> None:
        self.ids = ids
        self.selects: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.selects.append(params)
        if params.get("rows") == 0:
            return {"response": {"numFound": len(self.ids), "docs": []}}
        cursor = params["cursorMark"]
        start = 0 if cursor == "*" else int(cursor)
        page = self.ids[start : start + 2]
        following = str(start + len(page)) if page else cursor
        return {
            "response": {"numFound": len(self.ids), "docs": [{"id": i} for i in page]},
            "nextCursorMark": following,
        }

    def add(self, collection: str, docs: list[dict[str, Any]], **_: Any) -> None:
        self.updates.extend(docs)


class FakeGraph:
    def execute(self, cypher: str, **params: Any) -> None:
        return None


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from oss_server.curation import tasks as curation_tasks

    c = Collection.objects.create(slug="texts", name="texts")
    other = Collection.objects.create(slug="other", name="other")
    s = Source.objects.create(collection=c, name="s", connector="filesystem")
    docs = []
    for i in range(5):
        tags = (
            [{"iri": FLOOD, "label": "Flood", "kind": "concept", "stage": "tag", "spans": []}]
            if i == 0
            else []
        )
        docs.append(
            Document.objects.create(
                id=f"{i}" * 32,
                collection=c,
                source=s,
                uri=f"file:///{i}",
                content_hash=str(i),
                status=Document.Status.INDEXED,
                tags=tags,
            )
        )
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
        pack=pack, iri=FLOOD, kind="concept", pref_label="Flood", scheme_slug="themes", path="flood"
    )
    Review.objects.create(collection=c, document=docs[0], kind="tag", target=FLOOD)
    # the index holds a document of another collection too: the job must stay in its own
    solr = FakeSolr([d.id for d in docs[1:]] + ["9" * 32])
    monkeypatch.setattr(server_services, "solr_client", lambda: solr)
    monkeypatch.setattr(server_services, "graph_client", lambda: FakeGraph())
    queued: list[int] = []
    monkeypatch.setattr(curation_tasks.run_bulk_tag_job, "delay", queued.append)
    curator = User.objects.create_user("cur")
    CollectionRole.objects.create(user=curator, collection=c, role="curator")
    reader = User.objects.create_user("rdr")
    CollectionRole.objects.create(user=reader, collection=c, role="reader")
    del other
    return {
        "docs": docs,
        "solr": solr,
        "queued": queued,
        "curator": {"Authorization": f"Bearer {issue_token(curator, 't')}"},
        "reader": {"Authorization": f"Bearer {issue_token(reader, 't')}"},
    }


def _post(client: Client, world: dict[str, Any], who: str = "curator", **body: Any) -> Any:
    data = {"collection": "texts", "q": "deluge", "iri": FLOOD, "decision": "accept", **body}
    return client.post(
        "/api/curation/bulk-tags", data=data, content_type="application/json", headers=world[who]
    )


@pytest.mark.django_db
def test_a_dry_run_counts_and_a_job_decides_every_document(
    client: Client, world: dict[str, Any]
) -> None:
    from oss_server.curation.tasks import run_bulk_tag_job

    world["solr"].ids = [d.id for d in world["docs"]]
    count = _post(client, world, dry_run=True)
    assert count.status_code == 200 and count.json()["matched"] == 5
    assert BulkTagJob.objects.count() == 0, "a dry run decides nothing"
    search = world["solr"].selects[-1]
    assert 'collection_s:"texts"' in search["fq"] and search["q"] == "deluge"

    r = _post(client, world, reason="all flood stories")
    assert r.status_code == 202, r.content
    job = r.json()
    assert job["status"] == "queued" and job["label"] == "Flood" and job["matched"] == 5
    run_bulk_tag_job(world["queued"].pop())
    done = client.get(f"/api/curation/bulk-tags/{job['id']}", headers=world["reader"]).json()
    assert done["status"] == "done" and done["decided"] == 5 and done["unapplied"] == 0, done
    proposals = Proposal.objects.filter(target=FLOOD).order_by("pk")
    kinds = sorted(p.kind for p in proposals)
    assert kinds == ["accept_tag"] + ["add_tag"] * 4, "accept where tagged, add elsewhere"
    assert all(p.reason == f"bulk tagging job {job['id']}: all flood stories" for p in proposals)
    assert all(p.decided_by and p.decided_by.username == "cur" for p in proposals)
    assert Review.objects.get(target=FLOOD).status == Review.Status.ACCEPTED, "settled"
    for d in world["docs"]:
        d.refresh_from_db()
        assert d.fields["curated"][FLOOD]["decision"] == "accept"
    assert {u["id"] for u in world["solr"].updates} == {d.id for d in world["docs"]}

    # the same bulk decision again changes nothing; a reject is a new decision on each
    r = _post(client, world)
    run_bulk_tag_job(world["queued"].pop())
    again = BulkTagJob.objects.get(pk=r.json()["id"])
    assert again.unchanged == 5 and again.decided == 0
    r = _post(client, world, decision="reject")
    run_bulk_tag_job(world["queued"].pop())
    assert BulkTagJob.objects.get(pk=r.json()["id"]).decided == 5
    assert Proposal.objects.filter(kind__in=("reject_tag", "remove_tag")).count() == 5


@pytest.mark.django_db
def test_bulk_tagging_needs_a_curator_and_a_known_concept(
    client: Client, world: dict[str, Any]
) -> None:
    assert _post(client, world, who="reader").status_code == 403
    assert _post(client, world, iri="https://nowhere#x").status_code == 422
    assert _post(client, world, decision="maybe").status_code == 422
    listed = client.get("/api/curation/bulk-tags?collection=texts", headers=world["reader"])
    assert listed.status_code == 200 and listed.json() == []


@pytest.mark.django_db
def test_a_job_skips_documents_outside_its_collection(
    client: Client, world: dict[str, Any]
) -> None:
    from oss_server.curation.tasks import run_bulk_tag_job

    r = _post(client, world)
    run_bulk_tag_job(world["queued"].pop())
    job = BulkTagJob.objects.get(pk=r.json()["id"])
    assert job.status == "done" and job.matched == 5 and job.decided == 4
    assert not Proposal.objects.filter(document_id="9" * 32).exists()
