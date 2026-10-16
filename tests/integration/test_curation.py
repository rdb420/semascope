"""A review decision reaches every store and the outbox, and `oss reindex` keeps it.

The tests in this module run in order and share state: the decisions made by
the first are what the later ones look for.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx
import pytest
from _stack import collection, demo_texts, indexing_done, oss, wait_for

BASE = "https://semascope.dev/ontology/base#"

NOTE = "integration test decision"
decided: dict[str, Any] = {}


def last_event_id(api: httpx.Client) -> int:
    cursor = 0
    while True:
        page = api.get("/curation/events", params={"since_id": cursor, "limit": 1000}).json()
        if not page:
            return cursor
        cursor = page[-1]["id"]


def curated_edges(api: httpx.Client, doc_id: str, kind: str) -> dict[str, dict[str, Any]]:
    """CURATED edges from a document, keyed by the target node id."""

    params = {"id": f"Document:{doc_id}", "kinds": kind, "limit": 1000}
    out = api.get("/graph/neighbours", params=params).json()
    return {e["target"]: e["properties"] for e in out["edges"] if e["type"] == "CURATED"}


def doc_tags(api: httpx.Client, doc_id: str) -> dict[str, dict[str, Any]]:
    tags = api.get(f"/curation/documents/{doc_id}/tags").raise_for_status().json()
    return {t["iri"]: t for t in tags}


def is_rejected(api: httpx.Client, doc_id: str, iri: str) -> bool:
    """A rejected tag is gone from the document or kept with the decision on it."""

    tag = doc_tags(api, doc_id).get(iri)
    return tag is None or (tag["curated"] or {}).get("decision") == "reject"


def curated_filter(api: httpx.Client, value: str) -> set[str]:
    out = api.post(
        "/search",
        json={"q": "*:*", "collection": collection(), "filters": {"curated_ss": [value]}},
    ).json()
    return {h["id"] for h in out["hits"]}


def in_solr(api: httpx.Client, doc_id: str, value: str) -> bool:
    """Wait out Solr's soft commit (2 s) for an atomic update to become searchable."""

    return bool(
        wait_for(
            lambda: doc_id in curated_filter(api, value),
            timeout=60,
            every=1.0,
            what=f"curated_ss:{value} on {doc_id}",
        )
    )


def test_accepting_and_rejecting_reviews(api: httpx.Client, indexed: dict[str, Any]) -> None:
    open_items = api.get(
        "/curation/reviews", params={"collection": collection(), "limit": 500}
    ).json()
    tag = next((r for r in open_items if r["kind"] == "tag"), None)
    link = next((r for r in open_items if r["kind"] == "link"), None)
    assert tag and link, f"need an open tag and an open link review: {open_items}"
    cursor = last_event_id(api)

    accepted = api.post(
        f"/curation/reviews/{tag['id']}/decision", json={"decision": "accept", "note": NOTE}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    rejected = api.post(f"/curation/reviews/{link['id']}/decision", json={"decision": "reject"})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    events = api.get("/curation/events", params={"since_id": cursor}).json()
    by_kind = {e["kind"]: e for e in events}
    assert {"proposal.accept_tag", "proposal.reject_tag"} <= set(by_kind), events
    accept_event = by_kind["proposal.accept_tag"]
    assert accept_event["subject"] == tag["target"]
    assert accept_event["collection"] == collection()
    assert accept_event["payload"]["document_id"] == tag["document_id"]
    assert accept_event["payload"]["reason"] == NOTE
    assert accept_event["payload"]["external_ref"]
    assert by_kind["proposal.reject_tag"]["subject"] == link["target"]
    assert all(e["id"] > cursor for e in events)
    # the cursor moves on: nothing new after the last event
    assert api.get("/curation/events", params={"since_id": events[-1]["id"]}).json() == []

    proposals = api.get(
        "/curation/proposals", params={"collection": collection(), "kind": "accept_tag"}
    ).json()
    assert any(p["target"] == tag["target"] and p["applied"] for p in proposals), proposals
    decided.update(tag=tag, link=link, cursor=cursor)


def test_the_decision_is_in_every_store(api: httpx.Client) -> None:
    if not decided:
        pytest.skip("no decision was made")
    tag, link = decided["tag"], decided["link"]
    tags = doc_tags(api, tag["document_id"])
    assert tags[tag["target"]]["curated"]["decision"] == "accept"
    assert is_rejected(api, link["document_id"], link["target"])
    assert in_solr(api, link["document_id"], f"{link['target']}:reject")
    assert in_solr(api, tag["document_id"], f"{tag['target']}:accept")
    edges = curated_edges(api, tag["document_id"], "Concept")
    edge = edges.get(f"Concept:{tag['target']}")
    assert edge and edge["decision"] == "accept" and edge["note"] == NOTE, edges
    # named entities are keyed in the graph as "<Class>/<label>", as the graph stage writes them
    rejected = curated_edges(api, link["document_id"], "Entity")
    key = "Entity:" + link["target"].removeprefix(BASE)
    assert rejected.get(key, {}).get("decision") == "reject", rejected


def test_reindex_keeps_the_decision(api: httpx.Client) -> None:
    if not decided:
        pytest.skip("no decision was made")
    tag, link = decided["tag"], decided["link"]
    before = datetime.fromisoformat(
        api.get(f"/documents/{tag['document_id']}").json()["indexed_at"]
    )
    oss("reindex", "--collection", collection())

    def replayed() -> bool:
        doc = api.get(f"/documents/{tag['document_id']}").json()
        if doc["status"] != "indexed" or not doc["indexed_at"]:
            return False
        newer = datetime.fromisoformat(doc["indexed_at"]) > before
        return newer and bool(indexing_done(api, len(demo_texts())))

    wait_for(
        replayed,
        timeout=float(os.environ.get("SEMASCOPE_IT_TIMEOUT", "900")),
        every=5.0,
        what="the reindex",
    )
    tags = doc_tags(api, tag["document_id"])
    assert tags[tag["target"]]["curated"]["decision"] == "accept", tags.get(tag["target"])
    assert is_rejected(api, link["document_id"], link["target"]), "a rejected tag came back"
    assert in_solr(api, link["document_id"], f"{link['target']}:reject")
    assert in_solr(api, tag["document_id"], f"{tag['target']}:accept")
    edge = curated_edges(api, tag["document_id"], "Concept").get(f"Concept:{tag['target']}")
    assert edge and edge["decision"] == "accept", "the CURATED edge did not survive the reindex"
    reopened = api.get(
        "/curation/reviews", params={"collection": collection(), "limit": 500}
    ).json()
    decided_items = {(tag["document_id"], tag["target"]), (link["document_id"], link["target"])}
    again = [r for r in reopened if (r["document_id"], r["target"]) in decided_items]
    assert not again, f"decided reviews were reopened: {again}"
