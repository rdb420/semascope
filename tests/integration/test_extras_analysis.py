"""The rest of ADR-0012 on the live stack: analysis views (clusters, comparison, word tree,
diff), table and graph exports, synonym tooling, the ranking explanation and the Web
Annotation Protocol, through Caddy as a client sees them."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from _stack import DEMO, base_url, collection, compose, demo_texts, oss
from rdflib import RDF, Dataset, Graph, Namespace, URIRef
from rdflib.namespace import DCTERMS

BASE = Namespace("https://semascope.dev/ontology/base#")
ODYSSEUS = DEMO + "odysseus"


def need(*slugs: str) -> None:
    missing = [s for s in slugs if s not in demo_texts()]
    if missing:
        pytest.skip(f"needs the demo texts {missing} (SEMASCOPE_DEMO_ONLY)")


def anonymous() -> httpx.Client:
    return httpx.Client(base_url=base_url() + "/api", verify=False, timeout=60.0)  # noqa: S501


@pytest.fixture(scope="module")
def demo_reader() -> Iterator[httpx.Client]:
    """A user with the reader role on the demo collection only (not staff)."""

    code = (
        "from django.contrib.auth.models import User; "
        "from oss_server.api.auth import issue_token; "
        "from oss_server.catalogue.models import Collection, CollectionRole; "
        "u, _ = User.objects.get_or_create(username='it-extras2-reader'); "
        f"c = Collection.objects.get(slug='{collection()}'); "
        "CollectionRole.objects.get_or_create(user=u, collection=c, defaults={'role': 'reader'}); "
        "print('TOKEN=' + issue_token(u, 'it-extras2'))"
    )
    proc = compose("exec", "-T", "server", "oss-manage", "shell", "-c", code, timeout=120)
    token = next((ln[6:] for ln in proc.stdout.splitlines() if ln.startswith("TOKEN=")), "")
    if not token:
        pytest.fail(f"could not create a user: {proc.stdout}{proc.stderr}")
    with httpx.Client(
        base_url=base_url() + "/api",
        headers={"Authorization": f"Bearer {token}"},
        verify=False,  # noqa: S501 - Caddy's internal CA on a throwaway stack
        timeout=300.0,
    ) as client:
        yield client


def everything() -> dict[str, Any]:
    return {"q": "*:*", "collection": collection()}


# --- analysis views ----------------------------------------------------------------------------


def test_clusters_put_the_homeric_epics_together(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    need("odyssey", "iliad", "dhammapada", "song-celestial")
    for features in ("terms", "vectors"):
        body = (
            api.post(
                "/analytics/clusters",
                json={"search": everything(), "k": 2, "features": features},
                timeout=300,
            )
            .raise_for_status()
            .json()
        )
        assert body["documents"] == len(demo_texts()) and body["vector_error"] is None, body
        groups = [{d["id"] for d in c["documents"]} for c in body["clusters"]]
        epics = {docs_by_slug["odyssey"]["id"], docs_by_slug["iliad"]["id"]}
        assert any(epics <= g for g in groups), (features, body)
        assert all(c["terms"] for c in body["clusters"]), body
    assert anonymous().post("/analytics/clusters", json={"search": everything()}).status_code in (
        401,
        403,
    )


def test_compare_against_another_search_and_the_index(api: httpx.Client, indexed: Any) -> None:
    need("odyssey", "song-celestial")
    homer = {"q": "Odysseus", "collection": collection()}
    gita = {"q": "Arjuna", "collection": collection()}
    body = api.post("/analytics/compare", json={"search": homer, "against": gita}).json()
    assert body["background"] == "search" and body["a_documents"] >= 1, body
    assert "arjuna" in [t["term"] for t in body["less"]], body
    assert "arjuna" not in [t["term"] for t in body["more"]]
    index = api.post("/analytics/compare", json={"search": homer}).raise_for_status().json()
    assert index["background"] == "index" and index["b_documents"] == len(demo_texts())


def test_word_tree_of_a_phrase(api: httpx.Client, indexed: Any) -> None:
    need("odyssey")
    body = (
        api.post("/analytics/wordtree", json={"search": everything(), "phrase": "son of"})
        .raise_for_status()
        .json()
    )
    assert body["occurrences"] > 20 and body["root"]["word"].lower() == "son of", body["root"]
    kids = body["root"]["children"]
    assert kids and kids[0]["count"] >= kids[-1]["count"]
    left = api.post(
        "/analytics/wordtree", json={"search": everything(), "phrase": "son of", "side": "left"}
    ).json()
    assert left["side"] == "left" and left["occurrences"] == body["occurrences"]


def test_diff_two_documents(
    api: httpx.Client, demo_reader: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    need("odyssey", "iliad")
    a, b = docs_by_slug["odyssey"]["id"], docs_by_slug["iliad"]["id"]
    same = demo_reader.get("/analytics/diff", params={"a": a, "b": a}).raise_for_status().json()
    assert same["ratio"] == 1.0 and {h["op"] for h in same["hunks"]} <= {"equal", "skip"}
    other = demo_reader.get("/analytics/diff", params={"a": a, "b": b}, timeout=300)
    body = other.raise_for_status().json()
    assert body["ratio"] < 0.5 and body["a"]["title"] and body["b"]["title"]
    assert any(h["op"] in ("replace", "insert", "delete") for h in body["hunks"])


# --- exports -----------------------------------------------------------------------------------


def test_documents_and_entities_as_tables(
    demo_reader: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    resp = demo_reader.post("/export/documents", params={"format": "parquet"}, json=everything())
    assert resp.status_code == 200, resp.text[:300]
    table = pq.read_table(io.BytesIO(resp.content))
    assert table.num_rows == len(demo_texts())
    assert set(table.column("id").to_pylist()) == {d["id"] for d in docs_by_slug.values()}
    rows = list(
        csv.DictReader(
            io.StringIO(
                demo_reader.post("/export/documents", params={"format": "csv"}, json=everything())
                .raise_for_status()
                .text
            )
        )
    )
    assert len(rows) == len(demo_texts()) and all(r["concepts"] for r in rows)
    entities = demo_reader.post("/export/entities", params={"format": "jsonl"}, json=everything())
    lines = [json.loads(line) for line in entities.raise_for_status().text.splitlines()]
    assert any(r["iri"] == DEMO + "zeus" for r in lines), lines[:5]
    assert {r["collection"] for r in lines} == {collection()}


def test_graph_and_nquads_exports(
    demo_reader: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    need("odyssey")
    doc = docs_by_slug["odyssey"]["id"]
    ttl = demo_reader.get("/export/graph", params={"collection": collection()})
    g = Graph().parse(data=ttl.raise_for_status().text, format="turtle")
    node = URIRef(f"urn:semascope:doc:{doc}")
    assert (node, RDF.type, BASE.Document) in g
    assert (node, DCTERMS.subject, URIRef(DEMO + "zeus")) in g
    quads = demo_reader.get(f"/export/documents/{doc}", params={"format": "nquads"})
    ds = Dataset()
    ds.parse(data=quads.raise_for_status().text, format="nquads")
    assert (node, RDF.type, BASE.Document) in ds.graph(node)
    proc = oss("export", "graph", "--collection", collection(), "--format", "nquads")
    cli = Dataset()
    cli.parse(data=proc.stdout, format="nquads")
    assert len(cli.graph(URIRef(f"urn:semascope:collection:{collection()}"))) == len(g)


# --- synonyms ----------------------------------------------------------------------------------


def test_query_expansion_and_label_suggestions(api: httpx.Client, indexed: Any) -> None:
    out = api.get("/synonyms/expand", params={"q": "Ulysses"}).raise_for_status().json()
    match = next(m for m in out["matches"] if m["iri"] == ODYSSEUS)
    assert match["matched_type"] == "alt"
    assert "Odysseus" in [e["label"] for e in match["expands_to"]]
    assert out["analysis_error"] is None, out
    assert "odysseus" in {t["text"].lower() for t in out["tokens"]}, out["tokens"]
    need("odyssey")
    sug = api.get("/synonyms/suggest", params={"iri": ODYSSEUS, "collection": collection()})
    body = sug.raise_for_status().json()
    assert body["documents"] >= 1 and "Ulysses" in body["labels"]
    assert all(c["label"].lower() not in ("odysseus", "ulysses") for c in body["candidates"])


# --- ranking explain ---------------------------------------------------------------------------


def test_explain_a_hit(
    api: httpx.Client, demo_reader: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    need("odyssey")
    doc = docs_by_slug["odyssey"]["id"]
    search = {"q": "Odysseus Ithaca", "collection": collection(), "mode": "hybrid"}
    body = demo_reader.post("/explain", json={"search": search, "doc_id": doc}).json()
    kw = body["keyword"]
    assert kw["matched"] and kw["rank"] >= 1 and kw["clauses"], body
    assert not body["full"] and "idf" not in json.dumps(kw["explanation"]).lower()
    assert body["semantic"]["passages"] and body["fused"]["score"] > 0, body
    staff = api.post("/explain", json={"search": search, "doc_id": doc}).json()
    assert staff["full"] and "idf" in json.dumps(staff["keyword"]["explanation"]).lower()


# --- the Web Annotation Protocol ---------------------------------------------------------------


def test_annotation_protocol_round_trip(
    api: httpx.Client, demo_reader: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    need("odyssey")
    doc = docs_by_slug["odyssey"]["id"]
    anno = {
        "@context": "http://www.w3.org/ns/anno.jsonld",
        "type": "Annotation",
        "bodyValue": "it-extras2 protocol note",
        "target": {
            "source": f"urn:semascope:doc:{doc}",
            "selector": {"type": "TextQuoteSelector", "exact": "ingenious hero"},
        },
    }
    headers = {"Content-Type": 'application/ld+json; profile="http://www.w3.org/ns/anno.jsonld"'}
    denied = demo_reader.post("/annotations/w3c", content=json.dumps(anno), headers=headers)
    assert denied.status_code == 403
    made = api.post("/annotations/w3c", content=json.dumps(anno), headers=headers)
    assert made.status_code == 201, made.text
    url = made.headers["location"]
    path = "/annotations/w3c/" + url.rsplit("/", 1)[1]
    try:
        assert url.startswith(base_url())
        got = demo_reader.get(path)
        assert got.status_code == 200 and got.headers["etag"] == made.headers["etag"]
        assert got.json()["target"]["selector"][1]["exact"] == "ingenious hero"
        anno["bodyValue"] = "it-extras2 protocol note, edited"
        stale = api.put(path, content=json.dumps(anno), headers={**headers, "If-Match": '"x"'})
        assert stale.status_code == 412
        put = api.put(
            path, content=json.dumps(anno), headers={**headers, "If-Match": got.headers["etag"]}
        )
        assert put.status_code == 200 and put.headers["etag"] != got.headers["etag"], put.text
        container = api.get("/annotations/w3c", params={"document": doc})
        assert "BasicContainer" in container.headers["link"]
        gone = api.delete(path, headers={"If-Match": put.headers["etag"]})
        assert gone.status_code == 204
        assert api.get(path).status_code == 404
    finally:
        api.delete(path, headers={"If-Match": "*"})
