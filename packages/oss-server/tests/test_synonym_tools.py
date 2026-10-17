"""Synonym tooling (FUZZ-05, FUZZ-06, APP-08, APP-09): a query's expansion as the synonym rules
make it, pending labels, Solr's analysis tokens, and candidate labels for a concept."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server import services
from oss_server.api import searchplus
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole, Document, Source
from oss_server.knowledge import expansion
from oss_server.knowledge.models import Concept, Pack, PackDelta

ODYSSEUS = "https://p/people#odysseus"
FLOOD = "https://p/themes#flood"


def test_edit_distance_with_swaps_and_a_limit() -> None:
    assert expansion.distance("odysseus", "odysseus") == 0
    assert expansion.distance("odysseus", "odyseus") == 1
    assert expansion.distance("odysseus", "odyssues") == 1  # neighbours swapped
    assert expansion.distance("odysseus", "achilles", limit=2) == 3
    assert expansion.near("Odyseus", ["Odysseus"]) == 1
    assert expansion.near("ODYSSEUS", ["Odysseus"]) is None  # the label itself
    assert expansion.near("Penelope", ["Odysseus"]) is None


class SuggestSolr:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append({"path": path, **params})
        return {
            "analysis": {
                "field_names": {
                    "text_txt": {
                        "query": [
                            "org.apache.lucene.analysis.standard.StandardTokenizer",
                            [{"text": "Ulysses", "position": 1, "type": "<ALPHANUM>"}],
                            "org.apache.lucene.analysis.synonym.SynonymGraphFilter",
                            [
                                {"text": "odysseus", "position": 1, "type": "SYNONYM"},
                                {"text": "ulysses", "position": 1, "type": "<ALPHANUM>"},
                            ],
                        ]
                    }
                }
            }
        }

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.calls.append(params)
        if params.get("facet.contains"):
            return {
                "response": {"numFound": 9},
                "facet_counts": {
                    "facet_fields": {"persons_ss": ["Odyseus", 3, "Odin", 2, "Odysseus", 9]}
                },
            }
        if "facet.query" in params:
            counts = {q: (2 if "Telemachus" in q else 40) for q in params["facet.query"]}
            return {"response": {"numFound": 100}, "facet_counts": {"facet_queries": counts}}
        if "facet.field" in params:  # documents tagged with the concept
            return {
                "response": {"numFound": 4},
                "facet_counts": {
                    "facet_fields": {"persons_ss": ["Telemachus", 2, "Zeus", 2], "places_ss": []}
                },
            }
        return {"response": {"numFound": 0, "docs": []}}


@pytest.fixture
def world(db: None, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    solr = SuggestSolr()
    monkeypatch.setattr(services, "solr_client", lambda: solr)
    monkeypatch.setattr(searchplus, "solr_client", lambda: solr)
    expansion.clear()
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
        iri=ODYSSEUS,
        kind="individual",
        pref_label="Odysseus",
        alt_labels=["Ulysses"],
        hidden_labels=["Odysseas"],
    )
    Concept.objects.create(
        pack=pack, iri=FLOOD, kind="concept", pref_label="Flood", alt_labels=["Deluge", "the"]
    )
    a = Collection.objects.create(slug="a", name="a")
    b = Collection.objects.create(slug="b", name="b")
    reader = User.objects.create_user("reader")
    CollectionRole.objects.create(user=reader, collection=a, role="reader")
    for coll, label in ((a, "Odisseus"), (b, "Secret Name")):
        PackDelta.objects.create(
            pack_id="p",
            pack_version="1",
            collection=coll,
            kind="add_label",
            params={"iri": ODYSSEUS, "label": label, "label_type": "alt"},
        )
    s = Source.objects.create(collection=a, name="s", connector="filesystem")
    Document.objects.create(id="d" * 32, collection=a, source=s, uri="u", content_hash="h")
    return {"reader": reader, "solr": solr}


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user, 't')}"}


def test_expansions_prefer_the_longest_label_and_weigh_concepts() -> None:
    concept = type("C", (), {})()
    concept.pk, concept.kind, concept.pref_label = 1, "concept", "Trojan War"
    concept.extra, concept.alt_labels, concept.hidden_labels = {}, ["War of Troy"], []
    war = type("C", (), {})()
    war.pk, war.kind, war.pref_label = 2, "concept", "War"
    war.extra, war.alt_labels, war.hidden_labels = {}, ["Warfare"], []
    index = expansion.Index()
    for c in (concept, war):
        index.concepts[c.pk] = c
        for label, kind in expansion.typed_labels(c):
            index.by_form.setdefault(expansion.key(label), []).append(
                expansion.LabelRef(c.pk, label, kind)
            )
    words, found = expansion.expansions("the trojan war began", index)
    assert words == ["the", "trojan", "war", "began"]
    assert [(e.phrase, e.concept.pk) for e in found] == [("trojan war", 1)]
    assert found[0].weight_of_others == 0.5
    assert found[0].others == [("War of Troy", "alt")]


def test_expand_route_shows_matches_drafts_and_tokens(
    client: Client, world: dict[str, Any]
) -> None:
    resp = client.get(
        "/api/synonyms/expand?q=Ulysses%20and%20the%20flood", headers=_auth(world["reader"])
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    by_iri = {m["iri"]: m for m in body["matches"]}
    ulysses = by_iri[ODYSSEUS]
    assert ulysses["matched_label"] == "Ulysses" and ulysses["matched_type"] == "alt"
    assert {(e["label"], e["weight"]) for e in ulysses["expands_to"]} == {
        ("Odysseus", 1.0),
        ("Odysseas", 1.0),
    }
    flood = by_iri[FLOOD]
    assert [(e["label"], e["weight"]) for e in flood["expands_to"]] == [("Deluge", 0.5)]
    # the draft in a readable collection, not the one in b
    assert [d["label"] for d in body["drafts"]] == ["Odisseus"]
    assert [t["type"] for t in body["tokens"]] == ["SYNONYM", "<ALPHANUM>"]
    assert world["solr"].calls[-1]["path"] == "/documents/analysis/field"
    assert client.get("/api/synonyms/expand?q=", headers=_auth(world["reader"])).status_code == 422


def test_suggest_route_finds_variants_and_co_occurring_names(
    client: Client, world: dict[str, Any]
) -> None:
    resp = client.get(
        f"/api/synonyms/suggest?iri={quote(ODYSSEUS)}&collection=a", headers=_auth(world["reader"])
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["documents"] == 4 and "Ulysses" in body["labels"]
    labels = [c["label"] for c in body["candidates"]]
    assert labels[0] == "Odyseus"  # a spelling variant first
    first = body["candidates"][0]
    assert first["reasons"] == ["spelling"] and first["distance"] == 1
    assert "Odin" not in labels and "Odysseus" not in labels
    telemachus = next(c for c in body["candidates"] if c["label"] == "Telemachus")
    assert telemachus["reasons"] == ["co-occurs"] and telemachus["lift"] > 2
    assert "Zeus" not in labels  # as common outside the concept's documents
    tagged = [
        c for c in world["solr"].calls if "fq" in c and any("entity_iris_ss" in f for f in c["fq"])
    ]
    assert tagged  # an individual's documents are those linked to it
    denied = client.get(
        f"/api/synonyms/suggest?iri={quote(ODYSSEUS)}&collection=b", headers=_auth(world["reader"])
    )
    assert denied.status_code == 403
    missing = client.get(
        "/api/synonyms/suggest?iri=https://nowhere", headers=_auth(world["reader"])
    )
    assert missing.status_code == 404
