"""Direct tag decisions (add, reject, remove) and entity links reach Postgres, Solr and Neo4j.

Complements test_curation.py, which covers accept and reject through review
items. A rejected tag may be removed from the document or kept and flagged
by the curated decision; either way it leaves the Solr facets and the graph
keeps a CURATED edge with the decision.
"""

from __future__ import annotations

import secrets
from typing import Any

import httpx
import pytest
from _stack import collection, wait_for

IT = "https://semascope.dev/ontology/it#"


def tags_of(api: httpx.Client, doc_id: str) -> dict[str, dict[str, Any]]:
    return {t["iri"]: t for t in api.get(f"/curation/documents/{doc_id}/tags").json()}


def curated_edges(api: httpx.Client, doc_id: str, kind: str) -> dict[str, dict[str, Any]]:
    params = {"id": f"Document:{doc_id}", "kinds": kind, "limit": 1000}
    out = api.get("/graph/neighbours", params=params).raise_for_status().json()
    return {e["target"]: e["properties"] for e in out["edges"] if e["type"] == "CURATED"}


def solr_ids(api: httpx.Client, field: str, value: str) -> set[str]:
    body = {"q": "*:*", "collection": collection(), "filters": {field: [value]}, "facets": []}
    return {h["id"] for h in api.post("/search", json=body).raise_for_status().json()["hits"]}


def eventually(probe: Any, what: str) -> Any:
    """Wait out Solr's soft commit (about 2 s) for an atomic update."""

    return wait_for(probe, timeout=60, every=1.0, what=what)


def events_since(api: httpx.Client, cursor: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = api.get("/curation/events", params={"since_id": cursor}).json()
    return out


def last_event_id(api: httpx.Client) -> int:
    cursor = 0
    while page := api.get("/curation/events", params={"since_id": cursor, "limit": 1000}).json():
        cursor = page[-1]["id"]
    return cursor


def decide(api: httpx.Client, doc_id: str, iri: str, decision: str, label: str) -> dict[str, Any]:
    resp = api.post(
        f"/curation/documents/{doc_id}/tags",
        json={"iri": iri, "decision": decision, "label": label, "reason": "integration test"},
    )
    assert resp.status_code == 200, resp.text
    out: dict[str, Any] = resp.json()
    assert out["applied"], out
    return out


def test_add_reject_and_remove_tags(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    doc = docs_by_slug["dhammapada"]["id"]
    run = secrets.token_hex(3)  # fresh targets, so a rerun on a kept stack decides anew
    added, removed = IT + f"added-{run}", IT + f"never-tagged-{run}"
    cursor = last_event_id(api)
    newest = api.get("/curation/proposals", params={"limit": 1000}).json()
    proposal_cursor = newest[-1]["id"] if newest else 0

    # add: a concept the machine did not tag
    assert decide(api, doc, added, "accept", "Added by a curator")["kind"] == "add_tag"
    tag = tags_of(api, doc)[added]
    assert tag["curated"]["decision"] == "accept" and tag["stage"] == "curated", tag
    eventually(lambda: doc in solr_ids(api, "concept_iris_ss", added), f"{added} in Solr")
    assert doc in solr_ids(api, "curated_ss", f"{added}:accept")
    edge = curated_edges(api, doc, "Concept").get(f"Concept:{added}")
    assert edge and edge["decision"] == "accept" and edge["note"] == "integration test", edge

    # reject it again: it leaves the facets, the edge records the reversal
    assert decide(api, doc, added, "reject", "")["kind"] == "reject_tag"
    tag = tags_of(api, doc).get(added)
    assert tag is None or tag["curated"]["decision"] == "reject", tag
    eventually(lambda: doc not in solr_ids(api, "concept_iris_ss", added), f"{added} gone")
    assert doc in solr_ids(api, "curated_ss", f"{added}:reject")
    assert curated_edges(api, doc, "Concept")[f"Concept:{added}"]["decision"] == "reject"

    # remove: a rejection of a tag that was never there is recorded all the same
    assert decide(api, doc, removed, "reject", "Never tagged")["kind"] == "remove_tag"
    eventually(lambda: doc in solr_ids(api, "curated_ss", f"{removed}:reject"), "remove_tag")
    assert curated_edges(api, doc, "Concept")[f"Concept:{removed}"]["decision"] == "reject"

    kinds = [e["kind"] for e in events_since(api, cursor) if e["subject"] in (added, removed)]
    assert kinds == ["proposal.add_tag", "proposal.reject_tag", "proposal.remove_tag"], kinds

    # proposals read by cursor: the three decisions, in order, each with its own reference
    page = api.get("/curation/proposals", params={"since_id": proposal_cursor}).json()
    mine = [p for p in page if p["target"] in (added, removed)]
    assert [p["kind"] for p in mine] == ["add_tag", "reject_tag", "remove_tag"], mine
    assert len({p["external_ref"] for p in mine}) == 3
    assert all(p["id"] > proposal_cursor and p["document_id"] == doc for p in mine)
    after = api.get("/curation/proposals", params={"since_id": mine[-1]["id"]}).json()
    assert not [p for p in after if p["target"] in (added, removed)]


def test_an_unknown_decision_is_refused(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    doc = docs_by_slug["dhammapada"]["id"]
    resp = api.post(f"/curation/documents/{doc}/tags", json={"iri": IT + "x", "decision": "maybe"})
    assert resp.status_code == 422, resp.text


def open_link_review(api: httpx.Client) -> dict[str, Any]:
    """An open link review whose document has no tag yet for its first candidate."""

    reviews = api.get(
        "/curation/reviews", params={"collection": collection(), "kind": "link", "limit": 500}
    ).json()
    fallback = None
    for review in reviews:
        candidates = review["payload"].get("candidates") or []
        if not candidates or not review["document_id"]:
            continue
        fallback = fallback or review
        if candidates[0]["iri"] not in tags_of(api, review["document_id"]):
            return dict(review)
    if fallback is None:
        pytest.fail(f"no open link review with a candidate: {reviews[:3]}")
    return dict(fallback)


linked: dict[str, Any] = {}


def test_linking_an_entity_to_a_pack_individual(api: httpx.Client) -> None:
    review = open_link_review(api)
    doc, entity = review["document_id"], review["target"]
    individual = review["payload"]["candidates"][0]
    cursor = last_event_id(api)

    resp = api.post(
        f"/curation/reviews/{review['id']}/decision",
        json={"decision": "accept", "link_to": individual["iri"], "note": "same person"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "accepted"
    again = api.post(f"/curation/reviews/{review['id']}/decision", json={"decision": "reject"})
    assert again.status_code == 409, again.text  # a decided review stays decided

    events = [e for e in events_since(api, cursor) if e["kind"] == "proposal.link_entity"]
    assert events and events[0]["subject"] == entity, events
    assert events[0]["payload"]["params"]["iri"] == individual["iri"]
    proposals = api.get(
        "/curation/proposals", params={"collection": collection(), "kind": "link_entity"}
    ).json()
    assert any(p["target"] == entity and p["applied"] for p in proposals), proposals

    # Postgres: the entity's tag now points at the individual, with the link recorded
    tags = tags_of(api, doc)
    tag = tags[individual["iri"]]
    curated = tag["curated"]
    assert curated["decision"] == "accept" and curated["linked_from"] == entity, tag
    assert tag["pack_id"] and tag["pack_version"], "the link records the individual's pack"
    assert tag["stage"] == "curated" and tag["confidence"] == 1.0, tag
    assert entity not in tags, "the unlinked entity tag is still there"
    # graph: a CURATED edge to the individual's entity node
    edge = curated_edges(api, doc, "Entity").get(f"Entity:{individual['iri']}")
    assert edge and edge["decision"] == "accept" and edge["note"] == "same person", edge
    # Solr: the decision is searchable
    eventually(
        lambda: doc in solr_ids(api, "curated_ss", f"{individual['iri']}:accept"), "the link"
    )
    linked.update(doc=doc, iri=individual["iri"])


def test_link_reviews_need_a_target(api: httpx.Client) -> None:
    review = open_link_review(api)
    resp = api.post(f"/curation/reviews/{review['id']}/decision", json={"decision": "accept"})
    assert resp.status_code == 422, resp.text
    assert api.get("/curation/reviews", params={"kind": "link", "limit": 500}).status_code == 200


def test_a_linked_document_is_one_of_the_individuals_documents(api: httpx.Client) -> None:
    if not linked:
        pytest.skip("no entity was linked")
    doc, iri = linked["doc"], linked["iri"]
    tag = tags_of(api, doc)[iri]
    assert tag["pack_id"], f"the linked tag has no pack: {tag}"
    assert doc in solr_ids(api, "entity_iris_ss", iri)
    listed = api.get(
        "/knowledge/concepts/documents",
        params={"iri": iri, "collection": collection(), "rows": 100},
    ).json()
    assert doc in {d["id"] for d in listed["docs"]}, listed
