"""The indexed demo subset through the HTTP API: counts, pack tags, facets, search, graph."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import yaml
from _stack import DEMO, ROOT, collection, demo_texts

GOLDEN: dict[str, dict[str, list[str]]] = yaml.safe_load(
    (ROOT / "packs" / "demo" / "golden.yaml").read_text(encoding="utf-8")
)["texts"]
STRICT = ("entities", "traditions")  # every one must be found
LOOSE = ("text_types", "themes")  # counted towards a recall floor


def found_tags(api: httpx.Client, doc_id: str) -> set[str]:
    """Leaf slugs of every pack scheme path on a document (x_<scheme>_paths fields)."""

    fields = api.get(f"/documents/{doc_id}").raise_for_status().json()["fields"]
    return {
        path.rsplit("/", 1)[-1]
        for key, values in fields.items()
        if key.startswith("x_") and key.endswith("_paths")
        for path in values or []
    }


def search(api: httpx.Client, **body: Any) -> dict[str, Any]:
    out: dict[str, Any] = (
        api.post("/search", json={"collection": collection(), **body}).raise_for_status().json()
    )
    return out


# --- counts ------------------------------------------------------------------------------------


def test_every_document_is_indexed(api: httpx.Client, indexed: dict[str, Any]) -> None:
    expected = len(demo_texts())
    assert indexed["documents"] == {"indexed": expected}, indexed["documents"]
    assert indexed["solr_documents"] == expected
    assert indexed["collections"] >= 1 and indexed["sources"] >= 1
    chunks = indexed["chunk_models"].get(indexed["embed_model"], 0)
    assert chunks > expected, f"no chunk vectors for {indexed['embed_model']}: {indexed}"
    coll = api.get(f"/collections/{collection()}").raise_for_status().json()
    assert coll["document_count"] == expected
    sources = api.get(f"/collections/{collection()}/sources").raise_for_status().json()
    demo = next(s for s in sources if s["name"] == "demo-corpus")
    assert demo["status"] == "idle" and not demo["last_error"], demo
    assert demo["document_count"] == expected


def test_the_loaded_texts_are_the_subset(docs_by_slug: dict[str, dict[str, Any]]) -> None:
    assert sorted(docs_by_slug) == sorted(demo_texts())


def test_every_stage_ran(api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]) -> None:
    for slug, hit in docs_by_slug.items():
        doc = api.get(f"/documents/{hit['id']}").raise_for_status().json()
        stages = {r["stage"]: r.get("outcome") for r in doc["stage_runs"]}
        for stage in ("tag", "ner", "link", "embed", "index", "graph"):  # extract: not on reindex
            assert stage in stages, f"{slug}: {stage} did not run ({stages})"
        failed = {s: o for s, o in stages.items() if o == "failed"}
        assert not failed, f"{slug}: failed stages {failed}"


# --- pack tags against packs/demo/golden.yaml --------------------------------------------------


@pytest.mark.parametrize("slug", [s for s in demo_texts() if s in GOLDEN])
def test_golden_tags(api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]], slug: str) -> None:
    expect = GOLDEN[slug]
    found = found_tags(api, docs_by_slug[slug]["id"])
    for key in STRICT:
        missing = set(expect.get(key, [])) - found
        assert not missing, f"{slug}: expected {key} not tagged: {sorted(missing)}"
    wrong = set(expect.get("absent", [])) & found
    assert not wrong, f"{slug}: tagged with what golden.yaml says is absent: {sorted(wrong)}"


def test_golden_recall(api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]) -> None:
    want = got = 0
    missed: dict[str, list[str]] = {}
    for slug in (s for s in demo_texts() if s in GOLDEN):
        found = found_tags(api, docs_by_slug[slug]["id"])
        expected = {t for key in (*STRICT, *LOOSE) for t in GOLDEN[slug].get(key, [])}
        want, got = want + len(expected), got + len(expected & found)
        if expected - found:
            missed[slug] = sorted(expected - found)
    print(f"golden recall {got}/{want}; missed {missed}")
    assert want and got / want >= 0.9, f"recall {got}/{want}, missed {missed}"


def test_curation_endpoint_lists_pack_tags(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    tags = api.get(f"/curation/documents/{docs_by_slug['odyssey']['id']}/tags").json()
    odysseus = next(t for t in tags if t["iri"] == DEMO + "odysseus")
    assert odysseus["pack_id"] == "semascope-demo" and odysseus["span_count"] > 100


# --- facets ------------------------------------------------------------------------------------


def test_flat_facets(api: httpx.Client, indexed: dict[str, Any]) -> None:
    out = search(api, q="*:*")
    facets = {f: {v["value"]: v["count"] for v in vs} for f, vs in out["facets"].items()}
    n = len(demo_texts())
    assert facets["content_type_group_s"] == {"text": n}
    assert facets["language_s"] == {"en": n}
    assert facets["media_type_s"] == {"text/plain": n}


def test_hierarchical_facet(api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]) -> None:
    out = search(api, q="*:*")
    traditions = {v["value"]: v["count"] for v in out["facets"]["traditions_paths"]}
    assert traditions.get("european/greek", 0) >= 2, traditions
    # a parent counts at least the documents of each child
    for value, count in traditions.items():
        if "/" in value:
            parent = value.rsplit("/", 1)[0]
            assert traditions.get(parent, 0) >= count, (value, traditions)
    filtered = search(api, q="*:*", filters={"traditions_paths": ["european/greek"]})
    titles = {h["uri"].rsplit("/", 1)[-1] for h in filtered["hits"]}
    assert {"odyssey.txt", "iliad.txt"} <= titles, titles
    assert filtered["total"] == traditions["european/greek"]
    buddhist = search(api, q="*:*", filters={"traditions_paths": ["indian/buddhism"]})
    assert "dhammapada.txt" in {h["uri"].rsplit("/", 1)[-1] for h in buddhist["hits"]}
    assert "odyssey.txt" not in {h["uri"].rsplit("/", 1)[-1] for h in buddhist["hits"]}


# --- search modes ------------------------------------------------------------------------------


def test_keyword_search(api: httpx.Client, indexed: dict[str, Any]) -> None:
    out = search(api, q="Penelope", mode="keyword")
    uris = [h["uri"].rsplit("/", 1)[-1] for h in out["hits"]]
    assert "odyssey.txt" in uris, uris
    assert "dhammapada.txt" not in uris
    odyssey = next(h for h in out["hits"] if h["uri"].endswith("odyssey.txt"))
    assert any("<mark>" in s for s in odyssey["snippets"]), odyssey["snippets"]


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_vector_search(api: httpx.Client, indexed: dict[str, Any], mode: str) -> None:
    out = search(api, q="a king sails home after the war and finds suitors in his house", mode=mode)
    assert out["total"] > 0 and out["hits"], out
    top = out["hits"][0]
    assert top["passages"], f"{mode}: the top hit has no passages: {top}"
    greek = {h["uri"].rsplit("/", 1)[-1] for h in out["hits"][:2]}
    assert greek & {"odyssey.txt", "iliad.txt"}, [h["uri"] for h in out["hits"]]


def test_list_search(api: httpx.Client, indexed: dict[str, Any]) -> None:
    names = ["Odysseus", "Arjuna", "Xyzzy Nonesuch"]
    out = (
        api.post("/search/list", json={"names": names, "collection": collection()})
        .raise_for_status()
        .json()
    )
    assert "odyssey.txt" in {h["uri"].rsplit("/", 1)[-1] for h in out["Odysseus"]["hits"]}
    assert {h["uri"].rsplit("/", 1)[-1] for h in out["Arjuna"]["hits"]} == {"song-celestial.txt"}
    assert out["Odysseus"]["method"] == "phrase" and out["Odysseus"]["best_score"] > 0
    # nothing found by the phrase, the words or near spellings
    assert out["Xyzzy Nonesuch"] == {"total": 0, "hits": [], "method": None, "best_score": None}


# --- graph -------------------------------------------------------------------------------------


def test_graph_neighbours(api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]) -> None:
    out = (
        api.get("/graph/neighbours", params={"id": f"Entity:{DEMO}odysseus"})
        .raise_for_status()
        .json()
    )
    ids = {n["id"] for n in out["nodes"]}
    assert f"Document:{docs_by_slug['odyssey']['id']}" in ids, ids
    assert f"Document:{docs_by_slug['iliad']['id']}" in ids, ids
    assert all(e["type"] in ("TAGGED", "MENTIONS", "CURATED", "FROM", "IN") for e in out["edges"])
    hubs = api.get("/graph/shared", params={"collection": collection()}).json()
    assert any(h["key"] == f"{DEMO}hector" for h in hubs), hubs


def test_graph_shortest_path(api: httpx.Client, indexed: dict[str, Any]) -> None:
    a, b = f"Entity:{DEMO}odysseus", f"Entity:{DEMO}hector"
    out = api.get("/graph/path", params={"a": a, "b": b}).raise_for_status().json()
    ids = [n["id"] for n in out["nodes"]]
    assert a in ids and b in ids, ids
    assert 2 <= len(out["edges"]) <= 4, out["edges"]
    assert any(n["kind"] == "Document" for n in out["nodes"])
