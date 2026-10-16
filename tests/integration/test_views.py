"""Views of documents and searches on the real stack: originals, matches, table, entities,
the co-occurrence graph and the Atom/RSS feeds with their feed tokens."""

from __future__ import annotations

import hashlib
import secrets
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from _stack import base_url, collection, compose, demo_texts

ATOM = "{http://www.w3.org/2005/Atom}"


def _xml(content: bytes) -> ET.Element:
    return ET.fromstring(content)  # noqa: S314 - the stack's own feed under test


@pytest.fixture(scope="module")
def bare() -> Iterator[httpx.Client]:
    """A client with no credentials, the way a feed reader calls."""

    with httpx.Client(base_url=base_url() + "/api", verify=False, timeout=60.0) as client:  # noqa: S501
        yield client


def _odyssey(docs_by_slug: dict[str, dict[str, Any]]) -> dict[str, Any]:
    slug = "odyssey" if "odyssey" in docs_by_slug else sorted(docs_by_slug)[0]
    return docs_by_slug[slug]


# --- originals ----------------------------------------------------------------------------------


def test_original_is_the_stored_file(
    api: httpx.Client, bare: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    hit = _odyssey(docs_by_slug)
    name = hit["uri"].rsplit("/", 1)[-1]
    # identity encoding: Caddy marks the ETag of a compressed body with a suffix
    resp = api.get(f"/documents/{hit['id']}/original", headers={"Accept-Encoding": "identity"})
    assert resp.status_code == 200, resp.text
    body = resp.content
    # the ETag is the content hash the pipeline computed over the original
    assert resp.headers["etag"] == f'"{hashlib.sha256(body).hexdigest()}"'
    assert len(body) == hit["size"]
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.headers["content-disposition"] == f'inline; filename="{name}"'
    assert "nosniff" in resp.headers["x-content-type-options"]  # Caddy adds its own too
    assert resp.headers["accept-ranges"] == "bytes"
    download = api.get(f"/documents/{hit['id']}/original", params={"download": "true"})
    assert download.headers["content-disposition"] == f'attachment; filename="{name}"'
    # a byte range, as a viewer seeking in a large file asks
    part = api.get(f"/documents/{hit['id']}/original", headers={"Range": "bytes=100-199"})
    assert part.status_code == 206
    assert part.content == body[100:200]
    assert part.headers["content-range"] == f"bytes 100-199/{len(body)}"
    too_far = api.get(f"/documents/{hit['id']}/original", headers={"Range": f"bytes={len(body)}-"})
    assert too_far.status_code == 416
    # no credentials, no file; no document, no file
    assert bare.get(f"/documents/{hit['id']}/original").status_code == 401
    assert api.get(f"/documents/{'0' * 32}/original").status_code == 404


def test_query_matches_point_into_the_text(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    hit = _odyssey(docs_by_slug)
    text = api.get(f"/documents/{hit['id']}/text").raise_for_status().json()["text"]
    word = "Odysseus" if hit is docs_by_slug.get("odyssey") else text.split()[50].strip(".,;:")
    out = api.get(f"/documents/{hit['id']}/matches", params={"q": word}).raise_for_status().json()
    assert out["terms"] == [word.lower()] and out["total"] >= 1
    for span in out["spans"][:20]:
        assert text[span["start"] : span["end"]].lower().startswith(word.lower())
    access = api.get(f"/documents/{hit['id']}/access").raise_for_status().json()
    assert access["collection"] == collection() and access["can_curate"]  # the stack's admin


# --- table, entities, graph ---------------------------------------------------------------------


def test_table_stats_cover_every_match(api: httpx.Client, indexed: dict[str, Any]) -> None:
    out = (
        api.post("/views/table/stats", json={"q": "*:*", "collection": collection()})
        .raise_for_status()
        .json()
    )
    assert out["total"] == len(demo_texts())
    assert out["size"]["count"] == len(demo_texts()) and out["size"]["sum"] > 0
    assert out["indexed"]["min"] and out["indexed"]["max"]
    assert sum(v["count"] for v in out["languages"]) == len(demo_texts())


def test_entities_view_has_entity_facets(api: httpx.Client, indexed: dict[str, Any]) -> None:
    out = (
        api.post("/views/entities", json={"q": "*:*", "collection": collection()})
        .raise_for_status()
        .json()
    )
    fields = {f["field"] for f in out}
    assert fields & {"persons_ss", "entity_labels_ss", "places_ss"}, fields
    assert all(f["values"] for f in out)


def test_cooccurrence_of_a_search_and_its_edge_documents(
    api: httpx.Client, indexed: dict[str, Any]
) -> None:
    search = {"q": "*:*", "collection": collection()}
    graph = api.post("/views/graph", json={"search": search, "nodes": 30}).raise_for_status().json()
    assert graph["nodes"] and graph["edges"], graph
    heaviest = max(graph["edges"], key=lambda e: e["properties"]["docs"])
    assert heaviest["type"] == "CO_OCCURS" and heaviest["properties"]["docs"] >= 1
    docs = (
        api.post(
            "/views/graph/edge",
            json={"a": heaviest["source"], "b": heaviest["target"], "search": search},
        )
        .raise_for_status()
        .json()
    )
    # every document where both occur (a curated link may add one the count left out)
    assert heaviest["properties"]["docs"] <= len(docs["documents"]) <= len(demo_texts())
    assert {d["collection"] for d in docs["documents"]} == {collection()}
    # around one node: every edge starts there
    around = (
        api.post("/views/graph", json={"node": heaviest["source"], "collection": collection()})
        .raise_for_status()
        .json()
    )
    assert around["edges"] and {e["source"] for e in around["edges"]} == {heaviest["source"]}


# --- feeds --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def saved(api: httpx.Client) -> Iterator[dict[str, Any]]:
    name = "it-feed-" + secrets.token_hex(3)
    query = {"q": "Troy", "collection": collection()}
    s = api.post("/search/saved", json={"name": name, "query": query, "alert": True})
    assert s.status_code == 201, s.text
    yield s.json()
    api.delete(f"/search/saved/{s.json()['id']}")


def test_feeds_by_feed_token(
    api: httpx.Client,
    bare: httpx.Client,
    saved: dict[str, Any],
    docs_by_slug: dict[str, dict[str, Any]],
) -> None:
    token = api.post("/feeds/token").raise_for_status().json()["token"]
    ids = {h["id"] for h in docs_by_slug.values()}
    atom = bare.get(f"/feeds/saved/{saved['id']}", params={"token": token})
    assert atom.status_code == 200, atom.text
    assert atom.headers["content-type"] == "application/atom+xml; charset=utf-8"
    entries = _xml(atom.content).findall(f"{ATOM}entry")
    assert entries
    for entry in entries:
        link = entry.find(f"{ATOM}link")
        assert link is not None
        assert link.get("href", "").startswith(base_url() + "/documents/")
        assert link.get("href", "").rsplit("/", 1)[-1] in ids
    rss = bare.get(f"/feeds/saved/{saved['id']}", params={"token": token, "format": "rss"})
    assert rss.status_code == 200 and _xml(rss.content).findall("channel/item")
    query = bare.get(
        "/feeds/search", params={"q": "Odysseus", "collection": collection(), "token": token}
    )
    assert query.status_code == 200 and _xml(query.content).findall(f"{ATOM}entry")
    alerts = bare.get("/feeds/alerts", params={"token": token})
    categories = {c.get("term") for c in _xml(alerts.content).iter(f"{ATOM}category")}
    assert saved["name"] in categories
    # a feed token opens feeds and nothing else
    assert bare.get("/collections", params={"token": token}).status_code == 401
    assert bare.get(f"/documents/{next(iter(ids))}", params={"token": token}).status_code == 401
    # renewing cuts the old token off
    renewed = api.post("/feeds/token").raise_for_status().json()["token"]
    assert bare.get(f"/feeds/saved/{saved['id']}", params={"token": token}).status_code == 401
    assert bare.get(f"/feeds/saved/{saved['id']}", params={"token": renewed}).status_code == 200
    api.delete("/feeds/token").raise_for_status()
    assert bare.get(f"/feeds/saved/{saved['id']}", params={"token": renewed}).status_code == 401


def test_a_feed_token_reads_as_its_owner(
    api: httpx.Client, bare: httpx.Client, saved: dict[str, Any]
) -> None:
    """Another user's feed token does not open this user's saved search."""

    username = "it-feeds-" + secrets.token_hex(3)
    code = (
        "from django.contrib.auth.models import User; "
        "from oss_server.api.feeds import issue_feed_token; "
        f"u = User.objects.create_user({username!r}); "
        "print('TOKEN=' + issue_feed_token(u))"
    )
    proc = compose("exec", "-T", "server", "oss-manage", "shell", "-c", code, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    token = next(
        line.removeprefix("TOKEN=")
        for line in proc.stdout.splitlines()
        if line.startswith("TOKEN=")
    )
    try:
        assert bare.get(f"/feeds/saved/{saved['id']}", params={"token": token}).status_code == 404
        assert bare.get("/feeds/alerts", params={"token": token}).status_code == 200
    finally:
        compose(
            "exec",
            "-T",
            "server",
            "oss-manage",
            "shell",
            "-c",
            "from django.contrib.auth.models import User; "
            f"User.objects.filter(username={username!r}).delete()",
            timeout=120,
        )
