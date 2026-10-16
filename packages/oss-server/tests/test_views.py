"""Views of documents and searches (api/views.py, files.py, feeds.py).

Originals (decision D-2), query matches on the document page, the table,
entities and alert views, the co-occurrence graph and its edges, and the
Atom/RSS feeds with their feed tokens. Solr and Neo4j are fakes that record
what they were asked; the deployment default role is `none`, so collection
roles alone decide who reads what.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server.api import feeds, files, views
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import (
    ApiToken,
    Collection,
    CollectionRole,
    Document,
    SavedSearch,
    Source,
)

ATOM = "{http://www.w3.org/2005/Atom}"


def _xml(content: bytes) -> ET.Element:
    return ET.fromstring(content)  # noqa: S314 - the server's own feed under test


@pytest.fixture(autouse=True)
def _fast_passwords(settings: Any) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


class RecordingSolr:
    """Answers every select with `self.body` and records the parameters."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.body: dict[str, Any] = {"response": {"numFound": 0, "docs": []}, "facet_counts": {}}

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.calls.append({"collection": collection, **params})
        return self.body


@pytest.fixture
def solr(monkeypatch: pytest.MonkeyPatch) -> RecordingSolr:
    from oss_server import services

    fake = RecordingSolr()
    monkeypatch.setattr(services, "solr_client", lambda: fake)
    return fake


@pytest.fixture
def world(db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    monkeypatch.setenv("SEMASCOPE_DATA_DIR", str(tmp_path / "data"))
    a = Collection.objects.create(slug="a", name="a")
    b = Collection.objects.create(slug="b", name="b")
    alice = User.objects.create_user("alice")  # reader on a
    carol = User.objects.create_user("carol")  # curator on a
    bob = User.objects.create_user("bob")  # reader on b
    CollectionRole.objects.create(user=alice, collection=a, role="reader")
    CollectionRole.objects.create(user=carol, collection=a, role="curator")
    CollectionRole.objects.create(user=bob, collection=b, role="reader")
    src = Source.objects.create(collection=a, name="s", connector="filesystem")
    blobs = tmp_path / "data" / "blobs" / "ab"
    blobs.mkdir(parents=True)

    def doc(n: str, uri: str, media_type: str, content: bytes, **extra: Any) -> Document:
        blob = blobs / f"{n * 8}{Path(uri).suffix}"
        blob.write_bytes(content)
        return Document.objects.create(
            id=n * 32,
            collection=a,
            source=src,
            uri=uri,
            content_hash=n * 8,
            media_type=media_type,
            blob_path=str(blob),
            status=Document.Status.INDEXED,
            **extra,
        )

    pages = ["The ships sailed at dawn.", "No match here.", "A ship, then Ships again."]
    return {
        "alice": alice,
        "carol": carol,
        "bob": bob,
        "pdf": doc(
            "1", "file:///import/Report%20%C3%A9t%C3%A9.pdf", "application/pdf", b"%PDF-1.4 x"
        ),
        "html": doc(
            "2", "file:///import/page.html", "text/html; charset=UTF-8", b"<script>x</script>"
        ),
        "svg": doc("3", "upload://a/xyz/drawing.svg", "image/svg+xml", b"<svg/>"),
        "text": doc(
            "4",
            "file:///import/voyage.txt",
            "text/plain; charset=ISO-8859-1",
            bytes(range(48, 58)) * 10,  # 100 bytes of digits
            text="\n\n".join(pages),
            pages=pages,
        ),
        "tmp": tmp_path,
    }


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user, 't')}"}


def _post(client: Client, url: str, body: Any, user: User) -> Any:
    return client.post(url, data=body, content_type="application/json", headers=_auth(user))


# --- originals ----------------------------------------------------------------------------------


def test_original_is_served_with_its_type_and_name(world: dict[str, Any], client: Client) -> None:
    resp = client.get(f"/api/documents/{world['pdf'].id}/original", headers=_auth(world["alice"]))
    assert resp.status_code == 200
    assert resp.getvalue() == b"%PDF-1.4 x"
    assert resp["Content-Type"] == "application/pdf"
    assert resp["Content-Disposition"] == "inline; filename*=utf-8''Report%20%C3%A9t%C3%A9.pdf"
    assert resp["X-Content-Type-Options"] == "nosniff"
    assert resp["Accept-Ranges"] == "bytes"
    assert resp["ETag"] == '"11111111"'
    assert "Content-Security-Policy" not in resp  # the browser's PDF viewer needs no sandbox
    resp = client.get(
        f"/api/documents/{world['pdf'].id}/original?download=true", headers=_auth(world["alice"])
    )
    assert resp["Content-Disposition"].startswith("attachment;")


@pytest.mark.parametrize(
    ("key", "ctype", "name"),
    [("html", "text/html; charset=UTF-8", "page.html"), ("svg", "image/svg+xml", "drawing.svg")],
)
def test_active_content_is_only_ever_an_attachment(
    world: dict[str, Any], client: Client, key: str, ctype: str, name: str
) -> None:
    resp = client.get(f"/api/documents/{world[key].id}/original", headers=_auth(world["alice"]))
    assert resp.status_code == 200
    assert resp["Content-Type"] == ctype
    assert resp["Content-Disposition"] == f'attachment; filename="{name}"'
    assert resp["Content-Security-Policy"].startswith("sandbox")
    assert resp["X-Content-Type-Options"] == "nosniff"


def test_original_needs_reader_on_its_collection(world: dict[str, Any], client: Client) -> None:
    url = f"/api/documents/{world['pdf'].id}/original"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=_auth(world["bob"])).status_code == 403
    assert client.get(url, headers=_auth(world["carol"])).status_code == 200


def test_original_is_never_read_outside_the_blob_store(
    world: dict[str, Any], client: Client
) -> None:
    outside = world["tmp"] / "secret.txt"
    outside.write_text("secret")
    doc = world["text"]
    doc.blob_path = str(outside)
    doc.save()
    url = f"/api/documents/{doc.id}/original"
    assert client.get(url, headers=_auth(world["alice"])).status_code == 404
    (world["tmp"] / "data" / "secret.txt").write_text("secret")
    doc.blob_path = str(world["tmp"] / "data" / "blobs" / ".." / "secret.txt")
    doc.save()
    assert client.get(url, headers=_auth(world["alice"])).status_code == 404
    doc.blob_path = ""
    doc.save()
    resp = client.get(url, headers=_auth(world["alice"]))
    assert resp.status_code == 404 and "no stored original" in resp.json()["detail"]


def test_original_honours_a_byte_range(world: dict[str, Any], client: Client) -> None:
    url = f"/api/documents/{world['text'].id}/original"
    auth = _auth(world["alice"])
    resp = client.get(url, headers={**auth, "Range": "bytes=10-19"})
    assert resp.status_code == 206
    assert resp.getvalue() == b"0123456789"
    assert resp["Content-Range"] == "bytes 10-19/100"
    assert resp["Content-Length"] == "10"
    assert resp["Content-Type"] == "text/plain; charset=ISO-8859-1"
    resp = client.get(url, headers={**auth, "Range": "bytes=-5"})
    assert resp.status_code == 206 and resp.getvalue() == b"56789"
    resp = client.get(url, headers={**auth, "Range": "bytes=500-"})
    assert resp.status_code == 416 and resp["Content-Range"] == "bytes */100"
    resp = client.get(url, headers={**auth, "Range": "bytes=0-1,5-6"})
    assert resp.status_code == 200  # several ranges: the whole file
    resp = client.get(url, headers={**auth, "Range": "bytes=0-1", "If-Range": '"stale"'})
    assert resp.status_code == 200  # the file changed since: the whole file


@pytest.mark.parametrize(
    ("header", "size", "want"),
    [
        ("bytes=0-0", 10, (0, 0)),
        ("bytes=5-", 10, (5, 9)),
        ("bytes=5-100", 10, (5, 9)),
        ("bytes=-3", 10, (7, 9)),
        ("bytes=-30", 10, (0, 9)),
        ("items=0-1", 10, None),
        ("bytes=a-b", 10, None),
        ("bytes=", 10, None),
        ("bytes=1-2,4-5", 10, None),
    ],
)
def test_parse_range(header: str, size: int, want: tuple[int, int] | None) -> None:
    assert files.parse_range(header, size) == want


@pytest.mark.parametrize(
    ("header", "size"), [("bytes=10-", 10), ("bytes=5-2", 10), ("bytes=-0", 10), ("bytes=-1", 0)]
)
def test_parse_range_unsatisfiable(header: str, size: int) -> None:
    with pytest.raises(ValueError):
        files.parse_range(header, size)


def test_unknown_or_malformed_media_types_are_octet_stream(world: dict[str, Any]) -> None:
    doc = world["pdf"]
    for raw in ("", "not a type", "text/html\r\nX-Evil: 1"):
        doc.media_type = raw
        assert files.media_type(doc) == ("application/octet-stream", "application/octet-stream")


# --- the caller's role on a document ------------------------------------------------------------


def test_access_tells_readers_from_curators(world: dict[str, Any], client: Client) -> None:
    url = f"/api/documents/{world['pdf'].id}/access"
    reader = client.get(url, headers=_auth(world["alice"])).json()
    assert reader == {"collection": "a", "role": "reader", "can_curate": False, "can_edit": False}
    curator = client.get(url, headers=_auth(world["carol"])).json()
    assert curator["role"] == "curator" and curator["can_curate"] and not curator["can_edit"]
    assert client.get(url, headers=_auth(world["bob"])).status_code == 403


# --- query matches ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("q", "terms"),
    [
        ("*:*", []),
        ("", []),
        ("ship", ["ship"]),
        ('"wine dark" sea', ["wine dark", "sea"]),
        ("ship AND -storm NOT wreck", ["ship"]),
        ("title_txt:Odyssey collection_s:demo", ["odyssey"]),
        ("odys* sea^2 wave~1", ["odys*", "sea", "wave"]),
        ("modified_dt:[2020 TO 2021] (Troy OR Ithaca)", ["troy", "ithaca"]),
        ("{!knn f=v}[1,2]", []),
        ("a I x", []),
        ("Ulysses and Minerva", ["ulysses", "minerva"]),
        ('"the wine" the', ["the wine"]),
    ],
)
def test_query_terms(q: str, terms: list[str]) -> None:
    assert views.query_terms(q) == terms


def test_matches_give_offsets_pages_and_the_first_matching_page(
    world: dict[str, Any], client: Client
) -> None:
    doc = world["text"]
    url = f"/api/documents/{doc.id}/matches?q=ship"
    out = client.get(url, headers=_auth(world["alice"])).json()
    assert out["terms"] == ["ship"]
    assert [s["matched"] for s in out["spans"]] == ["ships", "ship", "Ships"]
    assert [s["page"] for s in out["spans"]] == [1, 3, 3]
    assert out["first_page"] == 1 and out["total"] == 3 and not out["truncated"]
    assert out["pages"] == [{"page": 1, "count": 1}, {"page": 3, "count": 2}]
    for s in out["spans"]:
        assert doc.text[s["start"] : s["end"]] == s["matched"]
    out = client.get(
        f"/api/documents/{doc.id}/matches?q=%22then%20ships%22&limit=1",
        headers=_auth(world["alice"]),
    ).json()
    assert out["first_page"] == 3 and [s["matched"] for s in out["spans"]] == ["then Ships"]
    out = client.get(
        f"/api/documents/{doc.id}/matches?q=sh*%20dawn&limit=2", headers=_auth(world["alice"])
    ).json()
    assert out["total"] == 4 and out["truncated"] and len(out["spans"]) == 2
    out = client.get(f"/api/documents/{doc.id}/matches?q=*:*", headers=_auth(world["alice"]))
    assert out.json()["spans"] == [] and out.json()["first_page"] is None
    assert client.get(url, headers=_auth(world["bob"])).status_code == 403


# --- table, entities and alerts -----------------------------------------------------------------


def test_table_stats_read_solr_stats_within_the_read_scope(
    world: dict[str, Any], client: Client, solr: RecordingSolr
) -> None:
    solr.body = {
        "response": {"numFound": 4, "docs": []},
        "stats": {
            "stats_fields": {
                "size_l": {"min": 10.0, "max": 90.0, "mean": 40.0, "sum": 160.0, "count": 4},
                "modified_dt": {
                    "min": "1900-01-01T00:00:00Z",
                    "max": "2020-01-01T00:00:00Z",
                    "count": 3,
                },
            }
        },
        "facet_counts": {"facet_fields": {"language_s": ["en", 3, "de", 1]}},
    }
    resp = _post(client, "/api/views/table/stats", {"q": "ship"}, world["alice"])
    assert resp.status_code == 200
    out = resp.json()
    assert out["total"] == 4 and out["size"]["mean"] == 40.0 and out["size"]["count"] == 4
    assert out["modified"] == {
        "min": "1900-01-01T00:00:00Z",
        "max": "2020-01-01T00:00:00Z",
        "count": 3,
    }
    assert out["pages"]["count"] == 0 and out["types"] == []
    assert out["languages"] == [{"value": "en", "count": 3}, {"value": "de", "count": 1}]
    call = solr.calls[-1]
    assert call["stats"] is True and "size_l" in call["stats.field"] and call["rows"] == 0
    assert 'collection_s:("a")' in call["fq"]
    resp = _post(client, "/api/views/table/stats", {"q": "x", "collection": "b"}, world["alice"])
    assert resp.status_code == 403


def test_entities_view_lists_the_entity_facets_in_order(
    world: dict[str, Any], client: Client, solr: RecordingSolr
) -> None:
    solr.body = {
        "response": {"numFound": 2, "docs": []},
        "facet_counts": {
            "facet_fields": {
                "places_ss": ["Ithaca", 2],
                "persons_ss": ["Odysseus", 2, "Penelope", 1],
                "x_deity_entities_ss": ["Athena", 1],
                "language_s": ["en", 2],
                "money_ss": [],
            }
        },
    }
    out = _post(client, "/api/views/entities?limit=5", {"q": "*:*"}, world["alice"]).json()
    assert [(f["field"], f["label"]) for f in out] == [
        ("persons_ss", "People"),
        ("places_ss", "Places"),
        ("x_deity_entities_ss", "Deity (found)"),
    ]
    assert out[0]["values"][0] == {"value": "Odysseus", "count": 2}
    call = solr.calls[-1]
    assert call["facet.limit"] == 5 and 'collection_s:("a")' in call["fq"]
    assert (
        _post(client, "/api/views/entities", {"collection": "b"}, world["alice"]).status_code == 403
    )


def test_alert_list_shows_each_saved_search_with_its_newest_matches(
    world: dict[str, Any], client: Client, solr: RecordingSolr
) -> None:
    alice = world["alice"]
    SavedSearch.objects.create(user=alice, name="ships", query={"q": "ship"}, alert=True)
    SavedSearch.objects.create(user=alice, name="b only", query={"q": "x", "collection": "b"})
    SavedSearch.objects.create(user=world["bob"], name="not mine", query={"q": "x"})
    solr.body = {
        "response": {
            "numFound": 1,
            "docs": [
                {
                    "id": "4" * 32,
                    "title_txt": "Voyage",
                    "collection_s": "a",
                    "indexed_dt": "2026-10-01T00:00:00Z",
                }
            ],
        }
    }
    out = client.get("/api/views/alerts", headers=_auth(alice)).json()
    assert [(a["name"], a["alert"]) for a in out] == [("ships", True), ("b only", False)]
    assert out[0]["total"] == 1 and out[0]["newest"][0]["title"] == "Voyage"
    assert out[1]["total"] is None and "reader role required" in out[1]["error"]
    assert solr.calls[0]["sort"] == "indexed_dt desc"


# --- graph --------------------------------------------------------------------------------------


class FakeNode(dict[str, Any]):
    def __init__(self, labels: list[str], **props: Any) -> None:
        super().__init__(props)
        self.labels = labels
        self.element_id = str(id(self))


class RecordingGraph:
    """Stands in for graph._raw: records each Cypher query and answers with `rows`."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[dict[str, Any]] = []

    def __call__(self, query: str, **params: Any) -> list[dict[str, Any]]:
        self.seen.append((query, params))
        return self.rows


@pytest.fixture
def cypher(monkeypatch: pytest.MonkeyPatch) -> RecordingGraph:
    from oss_server.api import graph

    fake = RecordingGraph()
    monkeypatch.setattr(graph, "_raw", fake)
    return fake


def test_cooccurrence_of_a_search_is_scoped_and_weighted(
    world: dict[str, Any],
    client: Client,
    solr: RecordingSolr,
    cypher: RecordingGraph,
) -> None:
    cypher.rows.append(
        {
            "a": FakeNode(["Entity"], key="odysseus", label="Odysseus"),
            "b": FakeNode(["Concept"], iri="https://x#greek", pref_label="Greek"),
            "shared": 3,
        }
    )
    solr.body = {"response": {"numFound": 2, "docs": [{"id": "1" * 32}, {"id": "4" * 32}]}}
    body = {"search": {"q": "ship", "filters": {"language_s": ["en"]}}}
    out = _post(client, "/api/views/graph", body, world["alice"]).json()
    assert [n["id"] for n in out["nodes"]] == ["Entity:odysseus", "Concept:https://x#greek"]
    assert out["edges"] == [
        {
            "source": "Entity:odysseus",
            "target": "Concept:https://x#greek",
            "type": "CO_OCCURS",
            "properties": {"docs": 3},
        }
    ]
    query, params = cypher.seen[-1]
    assert "d.collection IN $allowed" in query and params["allowed"] == ["a"]
    assert "d.id IN $ids" in query and params["ids"] == ["1" * 32, "4" * 32]
    solr_call = solr.calls[-1]
    assert solr_call["rows"] == 500 and solr_call["fl"] == "id"
    assert any("language_s" in str(f) for f in solr_call["fq"])
    # around one node, in one collection
    _post(
        client, "/api/views/graph", {"node": "Entity:odysseus", "collection": "a"}, world["alice"]
    )
    query, params = cypher.seen[-1]
    assert "(n:Entity {key: $key})" in query and params["key"] == "odysseus"
    assert "d.collection = $collection" in query and params["ids"] is None
    assert _post(client, "/api/views/graph", {"collection": "b"}, world["alice"]).status_code == 403
    bad = _post(client, "/api/views/graph", {"node": "Document:x"}, world["alice"])
    assert bad.status_code == 422


def test_edge_documents(world: dict[str, Any], client: Client, cypher: RecordingGraph) -> None:
    cypher.rows.extend(
        {"id": str(i) * 32, "title": f"T{i}", "collection": "a"} for i in range(1, 4)
    )
    body = {"a": "Entity:odysseus", "b": "Concept:https://x#greek", "limit": 2}
    out = _post(client, "/api/views/graph/edge", body, world["alice"]).json()
    assert [d["title"] for d in out["documents"]] == ["T1", "T2"] and out["more"] is True
    query, params = cypher.seen[-1]
    assert "(a:Entity {key: $a_key})" in query and "(b:Concept {iri: $b_key})" in query
    assert params["allowed"] == ["a"] and params["limit"] == 3
    body = {"a": f"Document:{'1' * 32}", "b": "Entity:odysseus"}
    _post(client, "/api/views/graph/edge", body, world["alice"])
    query, params = cypher.seen[-1]
    assert "(x:Entity {key: $key})" in query and params["key"] == "odysseus"
    body = {"a": "Document:x", "b": "Document:y"}
    assert _post(client, "/api/views/graph/edge", body, world["alice"]).status_code == 422
    body = {"a": "Nope:x", "b": "Entity:y"}
    assert _post(client, "/api/views/graph/edge", body, world["alice"]).status_code == 422


# --- feeds --------------------------------------------------------------------------------------

HIT = {
    "id": "4" * 32,
    "title_txt": "Voyage <home>",
    "uri_s": "file:///import/voyage.txt",
    "collection_s": "a",
    "indexed_dt": "2026-10-01T00:00:00Z",
}


def test_feed_token_lifecycle(world: dict[str, Any], client: Client) -> None:
    auth = _auth(world["alice"])
    assert client.get("/api/feeds/token", headers=auth).json() == {"token": None}
    first = client.post("/api/feeds/token", headers=auth).json()["token"]
    assert client.get("/api/feeds/token", headers=auth).json() == {"token": first}
    assert feeds.feed_user(first) == world["alice"]
    second = client.post("/api/feeds/token", headers=auth).json()["token"]
    assert second != first and feeds.feed_user(first) is None
    assert feeds.feed_user(second) == world["alice"]
    assert client.delete("/api/feeds/token", headers=auth).status_code == 204
    assert feeds.feed_user(second) is None
    assert feeds.feed_user("forged:token") is None
    assert not ApiToken.objects.filter(user=world["alice"], name="feeds").exists()


def test_saved_search_feed_by_token(
    world: dict[str, Any], client: Client, solr: RecordingSolr
) -> None:
    alice = world["alice"]
    saved = SavedSearch.objects.create(user=alice, name="voyages", query={"q": "voyage"})
    token = feeds.issue_feed_token(alice)
    solr.body = {
        "response": {"numFound": 1, "docs": [HIT]},
        "highlighting": {HIT["id"]: {"text_txt": ["the <mark>voyage</mark> & home"]}},
    }
    resp = client.get(f"/api/feeds/saved/{saved.id}?token={token}")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/atom+xml; charset=utf-8"
    root = _xml(resp.content)
    assert root.findtext(f"{ATOM}title") == "Semascope: voyages"
    entry = root.find(f"{ATOM}entry")
    assert entry is not None
    assert entry.findtext(f"{ATOM}title") == "Voyage <home>"
    assert entry.findtext(f"{ATOM}id") == f"urn:semascope:document:{HIT['id']}"
    assert entry.findtext(f"{ATOM}summary") == "the voyage & home"
    link = entry.find(f"{ATOM}link")
    assert link is not None and link.get("href", "").endswith(f"/documents/{HIT['id']}")
    assert solr.calls[-1]["sort"] == "indexed_dt desc"
    assert 'collection_s:("a")' in solr.calls[-1]["fq"]  # read as alice
    rss = client.get(f"/api/feeds/saved/{saved.id}?token={token}&format=rss")
    assert rss["Content-Type"] == "application/rss+xml; charset=utf-8"
    assert _xml(rss.content).find("channel/item/title").text == "Voyage <home>"  # type: ignore[union-attr]
    assert client.get(f"/api/feeds/saved/{saved.id}?token={token}&format=x").status_code == 422


def test_feed_tokens_are_refused_when_wrong_and_open_nothing_else(
    world: dict[str, Any], client: Client, solr: RecordingSolr
) -> None:
    alice, bob = world["alice"], world["bob"]
    saved = SavedSearch.objects.create(user=alice, name="s", query={"q": "x"})
    token = feeds.issue_feed_token(alice)
    assert client.get(f"/api/feeds/saved/{saved.id}?token=nope").status_code == 401
    assert client.get(f"/api/feeds/saved/{saved.id}").status_code == 401
    # another user's saved search is not found, even with a valid token
    bob_token = feeds.issue_feed_token(bob)
    assert client.get(f"/api/feeds/saved/{saved.id}?token={bob_token}").status_code == 404
    # a feed token is not an API credential
    assert client.get(f"/api/collections?token={token}").status_code == 401
    assert client.get(f"/api/search/saved?token={token}").status_code == 401
    # an inactive owner's token stops working
    alice.is_active = False
    alice.save()
    assert client.get(f"/api/feeds/saved/{saved.id}?token={token}").status_code == 401


def test_a_feed_follows_the_owners_read_access(
    world: dict[str, Any], client: Client, solr: RecordingSolr
) -> None:
    alice = world["alice"]
    saved = SavedSearch.objects.create(user=alice, name="s", query={"q": "x", "collection": "a"})
    token = feeds.issue_feed_token(alice)
    assert client.get(f"/api/feeds/saved/{saved.id}?token={token}").status_code == 200
    CollectionRole.objects.filter(user=alice).delete()
    assert client.get(f"/api/feeds/saved/{saved.id}?token={token}").status_code == 403
    resp = client.get(f"/api/feeds/search?q=x&token={token}")
    assert resp.status_code == 200 and "-*:*" in solr.calls[-1]["fq"]


def test_search_feed_takes_query_collection_and_filters(
    world: dict[str, Any], client: Client, solr: RecordingSolr
) -> None:
    token = feeds.issue_feed_token(world["alice"])
    solr.body = {"response": {"numFound": 1, "docs": [HIT]}}
    url = (
        "/api/feeds/search?q=voyage&collection=a&filter=language_s:en&filter=language_s:de"
        f"&token={token}"
    )
    resp = client.get(url)
    assert resp.status_code == 200 and b"Voyage" in resp.content
    fq = solr.calls[-1]["fq"]
    assert 'collection_s:"a"' in fq and any('language_s:("en" OR "de")' in str(f) for f in fq)
    assert client.get(f"/api/feeds/search?q=x&collection=b&token={token}").status_code == 403
    assert client.get(f"/api/feeds/search?filter=bad&token={token}").status_code == 422
    # a session or bearer token works too
    assert client.get("/api/feeds/search?q=x", headers=_auth(world["alice"])).status_code == 200


def test_alerts_feed_merges_alerting_searches(
    world: dict[str, Any], client: Client, solr: RecordingSolr
) -> None:
    alice = world["alice"]
    SavedSearch.objects.create(user=alice, name="one", query={"q": "x"}, alert=True)
    SavedSearch.objects.create(user=alice, name="two", query={"q": "y"}, alert=True)
    SavedSearch.objects.create(user=alice, name="quiet", query={"q": "z"}, alert=False)
    SavedSearch.objects.create(
        user=alice, name="lost", query={"q": "z", "collection": "b"}, alert=True
    )
    solr.body = {"response": {"numFound": 1, "docs": [HIT]}}
    token = feeds.issue_feed_token(alice)
    resp = client.get(f"/api/feeds/alerts?token={token}")
    assert resp.status_code == 200
    entries = _xml(resp.content).findall(f"{ATOM}entry")
    assert len(entries) == 1  # one document, matched by both searches
    terms = {c.get("term") for c in entries[0].findall(f"{ATOM}category")}
    assert terms == {"a", "one", "two"}
    assert len(solr.calls) == 2  # "quiet" has no alert; "lost" is no longer readable
