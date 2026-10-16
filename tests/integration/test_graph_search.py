"""Graph API (neighbours, path, hubs, the Cypher console) and search extras (fusion,
suggest, CSV export, list search) against the indexed demo subset."""

from __future__ import annotations

import csv
import io
from typing import Any

import httpx
import pytest
from _stack import DEMO, collection, demo_texts

# --- graph ---------------------------------------------------------------------------------


def node_ids(out: dict[str, Any]) -> set[str]:
    return {n["id"] for n in out["nodes"]}


def test_neighbours_depth_kinds_and_scope(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    odysseus = f"Entity:{DEMO}odysseus"
    one = api.get("/graph/neighbours", params={"id": odysseus}).raise_for_status().json()
    two = api.get("/graph/neighbours", params={"id": odysseus, "depth": 2, "limit": 1000}).json()
    assert node_ids(one) <= node_ids(two) and len(node_ids(two)) > len(node_ids(one))
    # depth 1 from an entity reaches documents only through MENTIONS, TAGGED or CURATED
    assert {n["kind"] for n in one["nodes"] if n["id"] != odysseus} <= {"Document"}

    only_docs = api.get(
        "/graph/neighbours", params={"id": odysseus, "depth": 2, "kinds": "Document"}
    ).json()
    assert {n["kind"] for n in only_docs["nodes"] if n["id"] != odysseus} == {"Document"}

    doc = docs_by_slug["dhammapada"]["id"]
    scoped = api.get(
        "/graph/neighbours",
        params={"id": f"Document:{doc}", "collection": "it-no-such-collection"},
    ).json()
    assert scoped["nodes"] == [] or node_ids(scoped) == set(), scoped

    bad = api.get("/graph/neighbours", params={"id": "Planet:earth"})
    assert bad.status_code == 422, bad.text


def test_path_respects_max_length(api: httpx.Client, indexed: dict[str, Any]) -> None:
    a, b = f"Entity:{DEMO}odysseus", f"Entity:{DEMO}hector"
    out = api.get("/graph/path", params={"a": a, "b": b}).raise_for_status().json()
    assert out["edges"], out
    shortest = len(out["edges"])
    too_short = api.get("/graph/path", params={"a": a, "b": b, "max_length": shortest - 1}).json()
    assert too_short["edges"] == [], too_short
    # consecutive edges share a node: it is a path, not a bag of edges
    ends = [(e["source"], e["target"]) for e in out["edges"]]
    for (s1, t1), (s2, t2) in zip(ends, ends[1:], strict=False):
        assert {s1, t1} & {s2, t2}, ends
    assert api.get("/graph/path", params={"a": "Nope:x", "b": b}).status_code == 422


def test_shared_concepts_and_entities(api: httpx.Client, indexed: dict[str, Any]) -> None:
    concepts = api.get(
        "/graph/shared", params={"collection": collection(), "kind": "Concept", "min_docs": 2}
    ).json()
    assert concepts and all(c["docs"] >= 2 for c in concepts), concepts
    assert all("Concept" in c["labels"] for c in concepts)
    counts = [c["docs"] for c in concepts]
    assert counts == sorted(counts, reverse=True)
    entities = api.get(
        "/graph/shared", params={"collection": collection(), "min_docs": len(demo_texts()) + 1}
    ).json()
    assert entities == [], "an entity in more documents than there are"
    assert api.get("/graph/shared", params={"kind": "Document"}).status_code == 422


def cypher(api: httpx.Client, query: str, limit: int = 100) -> httpx.Response:
    return api.post("/graph/cypher", json={"query": query, "limit": limit})


def test_cypher_console_reads(api: httpx.Client, indexed: dict[str, Any]) -> None:
    resp = cypher(api, "MATCH (d:Document) RETURN d.collection AS c, count(*) AS n ORDER BY c")
    assert resp.status_code == 200, resp.text
    rows = {r["c"]: r["n"] for r in resp.json()}
    assert rows.get(collection()) == len(demo_texts()), rows
    capped = cypher(api, "MATCH (n) RETURN n.key AS k", limit=3)
    assert capped.status_code == 200 and len(capped.json()) == 3, capped.text
    assert cypher(api, "MATCH (n RETURN n").status_code == 422  # syntax error, EXPLAIN-checked


@pytest.mark.parametrize(
    "query",
    [
        "CREATE (:Planted {by: 'it'})",
        "MATCH (d:Document) SET d.planted = true",
        "MERGE (:Planted {by: 'it'})",
        "MATCH (n:Planted) DETACH DELETE n",
        "MATCH (d:Document) WITH d LIMIT 1 CALL { WITH d SET d.planted = true } RETURN 1",
        "create (:Planted {by: 'lower case'})",
        "CALL db.createLabel('Planted')",  # a write procedure the keyword filter does not name
    ],
)
def test_cypher_console_refuses_writes(
    api: httpx.Client, indexed: dict[str, Any], query: str
) -> None:
    resp = cypher(api, query)
    assert resp.status_code == 422, f"{query!r} answered {resp.status_code}: {resp.text}"
    planted = cypher(
        api,
        "MATCH (n) WHERE n:Planted OR n.planted IS NOT NULL RETURN count(n) AS n",
    ).json()
    assert planted == [{"n": 0}], f"{query!r} wrote to the graph"
    labels = cypher(api, "CALL db.labels() YIELD label RETURN collect(label) AS labels").json()
    assert "Planted" not in labels[0]["labels"], f"{query!r} created a label"


# --- search ----------------------------------------------------------------------------------


def run_search(api: httpx.Client, **body: Any) -> dict[str, Any]:
    out: dict[str, Any] = (
        api.post("/search", json={"collection": collection(), "facets": [], **body})
        .raise_for_status()
        .json()
    )
    return out


def test_hybrid_is_reciprocal_rank_fusion(api: httpx.Client, indexed: dict[str, Any]) -> None:
    """Hybrid scores are 1/(60 + keyword rank) + 1/(60 + semantic rank), ranks from 0."""

    q = "Penelope weaving a shroud while suitors wait"
    size = 20  # every demo text fits on the page, so all ranks are visible
    keyword = [h["id"] for h in run_search(api, q=q, mode="keyword", page_size=size)["hits"]]
    semantic = [h["id"] for h in run_search(api, q=q, mode="semantic", page_size=size)["hits"]]
    hybrid = run_search(api, q=q, mode="hybrid", page_size=size)["hits"]
    assert keyword and semantic, (keyword, semantic)
    expected: dict[str, float] = {}
    for ranking in (semantic, keyword):
        for i, did in enumerate(ranking):
            expected[did] = expected.get(did, 0.0) + 1.0 / (60 + i)
    got = {h["id"]: h["score"] for h in hybrid}
    assert set(got) == set(expected), (got, expected)
    for did, score in got.items():
        assert score == pytest.approx(expected[did], abs=1e-5), (did, score, expected[did])
    scores = [h["score"] for h in hybrid]
    assert scores == sorted(scores, reverse=True)
    # a document found by both modes outranks one found by a single mode at the same rank
    both = set(keyword) & set(semantic)
    assert hybrid[0]["id"] in both, (hybrid[0]["id"], keyword, semantic)


def test_suggest_completes_titles(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    title = docs_by_slug["odyssey"]["title"]
    words = title.split()
    typed = " ".join([*words[:-1], words[-1][:4]])  # the last word half typed
    out = api.get("/suggest", params={"q": typed, "collection": collection()}).json()
    assert title in out["suggestions"], (typed, out)
    assert len(out["suggestions"]) <= 8
    none = api.get("/suggest", params={"q": "zzqxv", "collection": collection()}).json()
    assert none == {"suggestions": []}
    # Solr syntax in what is typed is escaped, not an error
    odd = api.get("/suggest", params={"q": 'odys" OR (*:*', "collection": collection()})
    assert odd.status_code == 200, odd.text
    assert api.get("/suggest", params={"q": "   "}).json() == {"suggestions": []}


def test_csv_export_matches_the_search(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    resp = api.post("/search/export.csv", json={"q": "*:*", "collection": collection()})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert {r["id"] for r in rows} == {h["id"] for h in docs_by_slug.values()}
    by_id = {r["id"]: r for r in rows}
    odyssey = docs_by_slug["odyssey"]
    assert by_id[odyssey["id"]]["uri"] == odyssey["uri"]
    assert by_id[odyssey["id"]]["collection"] == collection()
    assert by_id[odyssey["id"]]["media_type"] == "text/plain"
    filtered = api.post(
        "/search/export.csv",
        json={"q": "Penelope", "collection": collection(), "mode": "keyword"},
    )
    ids = {r["id"] for r in csv.DictReader(io.StringIO(filtered.text))}
    assert odyssey["id"] in ids and docs_by_slug["dhammapada"]["id"] not in ids


def test_list_search_counts_and_limits(api: httpx.Client, indexed: dict[str, Any]) -> None:
    out = api.post(
        "/search/list",
        json={
            "names": ["Hector", "  ", "Penelope"],
            "collection": collection(),
            "rows_per_name": 1,
        },
    ).json()
    assert set(out) == {"Hector", "Penelope"}, out  # blank names are skipped
    assert out["Hector"]["total"] >= 1 and len(out["Hector"]["hits"]) == 1
    assert out["Penelope"]["hits"][0]["uri"].endswith("odyssey.txt")
    too_many = api.post("/search/list", json={"names": ["x"] * 501})
    assert too_many.status_code == 422, too_many.text


def test_query_tagging_names_pack_concepts(api: httpx.Client, indexed: dict[str, Any]) -> None:
    out = api.post("/search/tag-query", json={"q": "Odysseus fights Hector"}).json()
    found = {c["id"]: c["phrase"] for c in out}
    assert found.get(f"{DEMO}odysseus") == "Odysseus", out
    assert found.get(f"{DEMO}hector") == "Hector", out
    assert all(c["score"] >= 90 for c in out)
    assert api.post("/search/tag-query", json={"q": "  "}).json() == []


def test_significant_terms_of_a_result_set(api: httpx.Client, indexed: dict[str, Any]) -> None:
    resp = api.post(
        "/search/terms", params={"top": 30}, json={"q": "Penelope", "collection": collection()}
    )
    assert resp.status_code == 200, resp.text
    terms = resp.json()
    assert 0 < len(terms) <= 30
    words = [t["term"] for t in terms]
    assert {"ulysses", "telemachus"} <= set(words), words  # the Odyssey, in Butler's names
    assert not {"the", "and", "of", "unto", "thou"} & set(words), words
    # ranked by how much more often the hits hold a word than the collection does (O-32)
    scores = [t["score"] for t in terms]
    assert scores == sorted(scores, reverse=True)
    assert all(t["count"] >= t["docs"] and t["background"] >= 0 for t in terms)
    assert all(1 <= t["docs"] <= len(demo_texts()) for t in terms)
    none = api.post("/search/terms", json={"q": "zzqxv", "collection": collection()}).json()
    assert none == []


SKOS = "http://www.w3.org/2004/02/skos/core#"
PACK_SPARQL = "/knowledge/packs/semascope-demo/sparql"


def test_sparql_over_a_pack_graph(api: httpx.Client, indexed: dict[str, Any]) -> None:
    query = f"SELECT ?label WHERE {{ <{DEMO}odysseus> <{SKOS}prefLabel> ?label }}"
    resp = api.post(PACK_SPARQL, json={"query": query})
    assert resp.status_code == 200, resp.text
    assert "Odysseus" in str(resp.json()), resp.json()
    capped = api.post(PACK_SPARQL, json={"query": "SELECT ?s WHERE { ?s ?p ?o }", "limit": 5})
    assert capped.status_code == 200 and len(capped.json()) == 5, capped.text
    ask = api.post(PACK_SPARQL, json={"query": f"ASK {{ <{DEMO}odysseus> ?p ?o }}"})
    assert ask.status_code == 200, ask.text
    for write in (
        f"INSERT DATA {{ <{DEMO}x> <{DEMO}y> <{DEMO}z> }}",
        f"DELETE WHERE {{ <{DEMO}odysseus> ?p ?o }}",
        "CLEAR ALL",
    ):
        refused = api.post(PACK_SPARQL, json={"query": write})
        assert refused.status_code == 422, (write, refused.text)
    still = api.post(PACK_SPARQL, json={"query": f"ASK {{ <{DEMO}odysseus> ?p ?o }}"})
    assert still.json() == ask.json()
    missing = api.post("/knowledge/packs/no-such-pack/sparql", json={"query": "ASK {}"})
    assert missing.status_code == 404


def test_sparql_over_a_pack_graph_with_prefixes(api: httpx.Client, indexed: dict[str, Any]) -> None:
    query = (
        f"PREFIX skos: <{SKOS}> SELECT ?label WHERE {{ <{DEMO}odysseus> skos:prefLabel ?label }}"
    )
    resp = api.post(PACK_SPARQL, json={"query": query})
    assert resp.status_code == 200, resp.text
    assert "Odysseus" in str(resp.json()), resp.json()
