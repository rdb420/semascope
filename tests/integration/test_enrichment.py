"""Enrichment against the real Solr Tagger and spaCy: offsets after astral characters, events.

One uploaded text in a collection of its own with the default profile (the
demo pack is active, so the collection tags with it).
"""

from __future__ import annotations

from typing import Any

import httpx
from _stack import ensure_collection, wait_for

SLUG = "it-enrich"
TEXT = (
    "\U0001f30a\U0001f30a\U0001f41a Odysseus sailed home to Ithaca. \U0001f3db\ufe0f "
    "Long after, scholars of World War II and of the Battle of Waterloo still read "
    "how Odysseus came back to Ithaca.\n"
)


def _upload(api: httpx.Client) -> dict[str, Any]:
    ensure_collection(api, SLUG, profile="default")
    files = [("files", ("voyage.txt", TEXT.encode("utf-8"), "text/plain"))]
    resp = api.post(f"/collections/{SLUG}/upload", files=files)
    assert resp.status_code == 202, resp.text
    (queued,) = resp.json()

    def settled() -> dict[str, Any] | None:
        doc: dict[str, Any] = api.get(f"/documents/{queued['id']}").raise_for_status().json()
        return doc if doc["status"] not in ("queued", "processing") else None

    doc: dict[str, Any] = wait_for(settled, timeout=600, what="the uploaded text to be indexed")
    assert doc["status"] == "indexed", doc["error"]
    return doc


def test_tag_spans_stay_on_their_words_after_emoji(api: httpx.Client) -> None:
    doc = _upload(api)
    text = api.get(f"/documents/{doc['id']}/text").raise_for_status().json()["text"]
    assert "\U0001f30a" in text, "the emoji survived extraction"
    tags = api.get(f"/curation/documents/{doc['id']}/tags").raise_for_status().json()
    dictionary = {t["label"]: t for t in tags if t["stage"] == "tag"}
    assert {"Odysseus", "Ithaca"} <= set(dictionary), sorted(dictionary)
    for label in ("Odysseus", "Ithaca"):
        # the tagger's own matches (NER mentions the link stage merged in have no `matched`)
        spans = [s for s in dictionary[label]["spans"] if s["matched"]]
        assert len(spans) == 2, dictionary[label]["spans"]
        # the tagger counts UTF-16 units: without the conversion each span would sit one
        # character to the right per emoji before it
        assert [text[s["start"] : s["end"]] for s in spans] == [label, label], spans
    runs = {r["stage"]: r for r in doc["stage_runs"]}
    assert runs["tag"]["version"] == "3" and doc["fields"]["tag_requests"] == 1


def test_ner_events_reach_the_events_facet(api: httpx.Client) -> None:
    doc = _upload(api)
    assert doc["fields"].get("events"), doc["fields"]
    body = {"q": "*:*", "collection": SLUG, "facets": ["events_ss"], "page_size": 0}

    def facet() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = (
            api.post("/search", json=body).raise_for_status().json()["facets"].get("events_ss")
            or []
        )
        return out

    values = wait_for(facet, timeout=60, every=1.0, what="the events facet")
    assert {v["value"] for v in values} >= set(doc["fields"]["events"]), values
