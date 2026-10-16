"""The proposal log is append-only (newest decision wins) and CURATED edges use graph keys."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_core.contracts import Tag
from oss_pipeline.stages.graph import entity_key
from oss_server import services as server_services
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, Document, Source
from oss_server.curation import services
from oss_server.curation.models import Event, Proposal

FLOOD = "https://p/themes#flood"
ODYSSEUS = "https://semascope.dev/ontology/base#Person/odysseus"


class FakeSolr:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def add(self, collection: str, docs: list[dict[str, Any]], **_: Any) -> None:
        self.updates.extend(docs)


class FakeGraph:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(self, cypher: str, **params: Any) -> None:
        self.calls.append({"cypher": cypher, **params})


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeSolr, FakeGraph]:
    solr, graph = FakeSolr(), FakeGraph()
    monkeypatch.setattr(server_services, "solr_client", lambda: solr)
    monkeypatch.setattr(server_services, "graph_client", lambda: graph)
    return solr, graph


def _doc(settings: dict[str, Any] | None = None) -> Document:
    c = Collection.objects.create(slug="log", name="log", settings=settings or {})
    s = Source.objects.create(collection=c, name="s", connector="filesystem")
    return Document.objects.create(
        id="e" * 32,
        collection=c,
        source=s,
        uri="file:///b",
        content_hash="h",
        status=Document.Status.INDEXED,
        tags=[
            {
                "iri": FLOOD,
                "label": "Flood",
                "kind": "concept",
                "stage": "tag",
                "confidence": 0.75,
                "spans": [{"start": 0, "end": 5}],
                "pack_id": "p",
                "pack_version": "1",
            },
            {
                "iri": ODYSSEUS,
                "label": "Odysseus",
                "kind": "entity",
                "stage": "ner",
                "confidence": 0.7,
                "spans": [],
            },
        ],
    )


def _decide(doc: Document, decision: str, target: str = FLOOD, **params: Any) -> Proposal:
    return services.propose(
        doc.collection,
        decision,
        target,
        params={"label": "Flood", "kind": "concept", **params},
        document=doc,
    )


@pytest.mark.django_db
def test_accept_reject_accept_ends_accepted(stores: tuple[FakeSolr, FakeGraph]) -> None:
    doc = _doc()
    a = _decide(doc, Proposal.Kind.ACCEPT_TAG)
    r = _decide(doc, Proposal.Kind.REJECT_TAG)
    doc.refresh_from_db()
    assert doc.fields["curated"][FLOOD]["decision"] == "reject"
    assert any(t["iri"] == FLOOD for t in doc.tags), "the machine's tag stays, flagged"
    solr, _ = stores
    assert FLOOD not in solr.updates[-1]["concept_iris_ss"]["set"], "but leaves the index"
    b = _decide(doc, Proposal.Kind.ACCEPT_TAG)
    assert len({a.pk, r.pk, b.pk}) == 3, "every decision is a new row"
    assert len({a.external_ref, r.external_ref, b.external_ref}) == 3
    assert all(p.applied for p in (a, r, b))
    doc.refresh_from_db()
    assert doc.fields["curated"][FLOOD]["decision"] == "accept"
    assert any(t["iri"] == FLOOD for t in doc.tags), "accepting again restores the tag"
    assert services.latest_proposal(doc.collection, FLOOD, doc) == b
    events = list(Event.objects.filter(kind__startswith="proposal.").order_by("pk"))
    assert [e.payload["external_ref"] for e in events] == [
        a.external_ref,
        r.external_ref,
        b.external_ref,
    ]
    assert events[2].payload["supersedes"] == r.external_ref
    assert events[0].payload["supersedes"] is None


@pytest.mark.django_db
def test_first_decision_keeps_the_rc2_reference(stores: tuple[FakeSolr, FakeGraph]) -> None:
    doc = _doc()
    a = _decide(doc, Proposal.Kind.ACCEPT_TAG)
    params = {"label": "Flood", "kind": "concept"}
    assert a.external_ref == Proposal.make_ref("log", "accept_tag", FLOOD, params, doc.id)


@pytest.mark.django_db
def test_repeating_the_decision_in_force_is_a_no_op(stores: tuple[FakeSolr, FakeGraph]) -> None:
    doc = _doc()
    a = _decide(doc, Proposal.Kind.REJECT_TAG)
    # the tag is gone now, so the UI sends remove_tag: the same decision, nothing new
    b = _decide(doc, Proposal.Kind.REMOVE_TAG)
    assert a.pk == b.pk
    assert Proposal.objects.count() == 1 and Event.objects.count() == 1


@pytest.mark.django_db
def test_a_retry_applies_a_decision_a_store_missed(
    stores: tuple[FakeSolr, FakeGraph], monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _doc()
    real = services.apply_proposal

    def down(p: Proposal) -> None:
        raise ConnectionError("solr down")

    monkeypatch.setattr(services, "apply_proposal", down)
    a = _decide(doc, Proposal.Kind.REJECT_TAG)
    assert not a.applied
    monkeypatch.setattr(services, "apply_proposal", real)
    b = _decide(doc, Proposal.Kind.REJECT_TAG)
    assert a.pk == b.pk and b.applied
    assert Proposal.objects.get(pk=a.pk).applied


@pytest.mark.django_db
def test_curated_edge_uses_the_graph_stage_entity_key(stores: tuple[FakeSolr, FakeGraph]) -> None:
    _, graph = stores
    doc = _doc()
    _decide(doc, Proposal.Kind.REJECT_TAG, ODYSSEUS, label="Odysseus", kind="entity")
    assert graph.calls[-1]["target"] == "Person/odysseus"
    assert "MERGE (x:Entity {key: $target})" in graph.calls[-1]["cypher"]
    _decide(doc, Proposal.Kind.ACCEPT_TAG)
    assert graph.calls[-1]["target"] == FLOOD and ":Concept {iri:" in graph.calls[-1]["cypher"]


@pytest.mark.django_db
def test_curated_edge_is_pseudonymised_like_the_graph_stage(
    stores: tuple[FakeSolr, FakeGraph],
) -> None:
    from oss_core.config import get_settings

    _, graph = stores
    doc = _doc({"pseudonymise": True})
    _decide(doc, Proposal.Kind.REJECT_TAG, ODYSSEUS, label="Odysseus", kind="entity")
    secret = get_settings().semascope_secret_key.get_secret_value()
    tag = Tag(iri=ODYSSEUS, label="Odysseus", kind="entity", stage="ner")
    assert graph.calls[-1]["target"] == entity_key(tag, secret)
    assert graph.calls[-1]["target"].startswith("h:")


@pytest.mark.django_db
def test_linking_keys_the_edge_by_the_individual_iri(stores: tuple[FakeSolr, FakeGraph]) -> None:
    _, graph = stores
    doc = _doc({"pseudonymise": True})
    services.propose(
        doc.collection,
        Proposal.Kind.LINK_ENTITY,
        ODYSSEUS,
        params={"iri": "https://myth/odysseus", "label": "Odysseus"},
        document=doc,
        pack_id="myth",
        pack_version="1",
    )
    assert graph.calls[-1]["target"] == "https://myth/odysseus"


@pytest.mark.django_db
def test_direct_tag_decisions_flip_back_and_forth(
    stores: tuple[FakeSolr, FakeGraph], client: Client
) -> None:
    doc = _doc()
    user = User.objects.create_user("flip", password="v" * 16, is_staff=True)
    auth = {"Authorization": f"Bearer {issue_token(user, 't')}"}
    for decision in ("accept", "reject", "accept"):
        r = client.post(
            f"/api/curation/documents/{doc.id}/tags",
            data={"iri": FLOOD, "decision": decision, "label": "Flood", "kind": "concept"},
            content_type="application/json",
            headers=auth,
        )
        assert r.status_code == 200, r.content
    doc.refresh_from_db()
    assert doc.fields["curated"][FLOOD]["decision"] == "accept"
    assert any(t["iri"] == FLOOD for t in doc.tags)
    # a rejected machine tag stays on the document (flagged), so accepting it again is an
    # accept_tag, not an add_tag
    assert list(Proposal.objects.order_by("pk").values_list("kind", flat=True)) == [
        "accept_tag",
        "reject_tag",
        "accept_tag",
    ]


def _active_concept(iri: str, label: str, scheme: str, paths: list[str]) -> None:
    from oss_server.knowledge.models import Concept, Pack

    pack, _ = Pack.objects.get_or_create(
        pack_id="p",
        version="1",
        defaults={
            "title": "P",
            "base_iri": "https://p/",
            "status": Pack.Status.ACTIVE,
            "manifest_hash": "m",
            "graph_iri": "urn:p",
        },
    )
    Concept.objects.create(
        pack=pack,
        iri=iri,
        kind="concept",
        pref_label=label,
        scheme_slug=scheme,
        path=paths[0],
        paths=paths,
    )


@pytest.mark.django_db
def test_a_rejected_concept_leaves_the_tree_facet_at_once(
    stores: tuple[FakeSolr, FakeGraph],
) -> None:
    """SEMA-030: the live update after a decision sets `<scheme>_paths` from the curated
    view, as the index stage does."""

    solr, _ = stores
    _active_concept(FLOOD, "Flood", "themes", ["water/flood"])
    doc = _doc()
    doc.fields.update(
        tagged_schemes=["themes"],
        x_themes_paths=["water/flood"],
        paths_by_tag={FLOOD: {"themes": ["water/flood"]}},
    )
    doc.save()
    _decide(doc, Proposal.Kind.REJECT_TAG)
    assert solr.updates[-1]["themes_paths"] == {"set": None}, "the scheme is left empty"
    _decide(doc, Proposal.Kind.ACCEPT_TAG)
    assert solr.updates[-1]["themes_paths"] == {"set": ["water", "water/flood"]}


@pytest.mark.django_db
def test_add_tag_of_a_new_iri_resolves_its_pack_and_paths(
    stores: tuple[FakeSolr, FakeGraph],
) -> None:
    """SEMA-055 and SEMA-030: a concept a curator adds carries its pack version and every
    path of its polyhierarchy into the tree facet."""

    solr, _ = stores
    descent = "https://p/themes#descent"
    _active_concept(descent, "Descent", "themes", ["afterlife/descent", "journey/descent"])
    doc = _doc()
    p = services.propose(
        doc.collection,
        Proposal.Kind.ADD_TAG,
        descent,
        params={"label": "Descent", "kind": "concept"},
        document=doc,
    )
    assert (p.pack_id, p.pack_version) == ("p", "1")
    doc.refresh_from_db()
    curated = doc.fields["curated"][descent]
    assert curated["pack_id"] == "p"
    assert curated["paths"] == {"themes": ["afterlife/descent", "journey/descent"]}
    assert solr.updates[-1]["themes_paths"] == {
        "set": ["afterlife", "afterlife/descent", "journey", "journey/descent"]
    }
    assert "p@1" in solr.updates[-1]["pack_versions_ss"]["set"]


@pytest.mark.django_db
def test_the_sweep_applies_what_a_store_missed(
    stores: tuple[FakeSolr, FakeGraph], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEMA-031: an unapplied decision is applied by the periodic sweep, not only by
    repeating it; a decision it superseded is settled with it."""

    from datetime import timedelta

    from django.utils import timezone

    solr, _ = stores
    doc = _doc()
    real = services.apply_proposal

    def down(p: Proposal) -> None:
        raise ConnectionError("solr down")

    monkeypatch.setattr(services, "apply_proposal", down)
    a = _decide(doc, Proposal.Kind.REJECT_TAG)
    b = _decide(doc, Proposal.Kind.ACCEPT_TAG)
    assert not a.applied and not b.applied
    doc.refresh_from_db()
    assert doc.fields["curated"][FLOOD]["decision"] == "accept", "Postgres has the decision"
    monkeypatch.setattr(services, "apply_proposal", real)
    assert services.retry_unapplied() == 0, "too fresh: the request may still be applying it"
    Proposal.objects.update(decided_at=timezone.now() - timedelta(minutes=5))
    assert services.retry_unapplied() == 1
    assert Proposal.objects.filter(applied=False).count() == 0
    assert solr.updates[-1]["curated_ss"] == {"set": [f"{FLOOD}:accept"]}


@pytest.mark.django_db
def test_solr_is_written_after_the_decision_commits(
    stores: tuple[FakeSolr, FakeGraph], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEMA-049: a store that fails cannot roll the decision back, and the outbox event
    and the document's record are written in the same transaction as the proposal."""

    doc = _doc()

    def down(p: Proposal) -> None:
        assert Event.objects.filter(subject=FLOOD).exists(), "the event is written first"
        raise ConnectionError("solr down")

    monkeypatch.setattr(services, "apply_proposal", down)
    p = _decide(doc, Proposal.Kind.REJECT_TAG)
    assert not p.applied
    doc.refresh_from_db()
    assert doc.fields["curated"][FLOOD]["decision"] == "reject"
