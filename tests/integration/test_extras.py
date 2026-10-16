"""ADR-0012 capabilities on the live stack: more like this, RDF exports, W3C annotations,
OpenSearch, the analytics overview and `oss eval`, through Caddy as a client sees them."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405 - the stack's own output
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from _stack import DEMO, ROOT, base_url, collection, compose, demo_texts, oss
from rdflib import RDF, Graph, Namespace, URIRef

BASE = Namespace("https://semascope.dev/ontology/base#")
OA = Namespace("http://www.w3.org/ns/oa#")
QUOTE = "Tell me, O Muse, of that ingenious hero"
NOTE = "the opening invocation"


def need(*slugs: str) -> None:
    missing = [s for s in slugs if s not in demo_texts()]
    if missing:
        pytest.skip(f"needs the demo texts {missing} (SEMASCOPE_DEMO_ONLY)")


def anonymous() -> httpx.Client:
    return httpx.Client(base_url=base_url() + "/api", verify=False, timeout=60.0)  # noqa: S501


@pytest.fixture(scope="module")
def plain_reader() -> Iterator[httpx.Client]:
    """A signed-in user with no role of their own: the deployment's default role applies."""

    code = (
        "from django.contrib.auth.models import User; "
        "from oss_server.api.auth import issue_token; "
        "u, _ = User.objects.get_or_create(username='it-extras-plain'); "
        "print('TOKEN=' + issue_token(u, 'it-extras'))"
    )
    proc = compose("exec", "-T", "server", "oss-manage", "shell", "-c", code, timeout=120)
    token = next((ln[6:] for ln in proc.stdout.splitlines() if ln.startswith("TOKEN=")), "")
    if not token:
        pytest.fail(f"could not create a user: {proc.stdout}{proc.stderr}")
    with httpx.Client(
        base_url=base_url() + "/api",
        headers={"Authorization": f"Bearer {token}"},
        verify=False,  # noqa: S501 - Caddy's internal CA on a throwaway stack
        timeout=120.0,
    ) as client:
        yield client


def streamed(resp: httpx.Response) -> str:
    assert resp.status_code == 200, resp.text[:500]
    return resp.text


# --- more like this ----------------------------------------------------------------------------


def test_similar_documents_by_words_meaning_and_both(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    need("odyssey", "iliad")
    odyssey, iliad = docs_by_slug["odyssey"]["id"], docs_by_slug["iliad"]["id"]
    others = [docs_by_slug[s]["id"] for s in ("dhammapada", "song-celestial") if s in docs_by_slug]
    for mode in ("text", "vector", "hybrid"):
        out = api.get(f"/documents/{odyssey}/similar", params={"mode": mode}).raise_for_status()
        body = out.json()
        assert body["text_error"] is None and body["vector_error"] is None, body
        ids = [h["id"] for h in body["hits"]]
        assert odyssey not in ids and iliad in ids, body
        # the other Homeric epic is more like the Odyssey than the Buddhist and Hindu texts
        for other in others:
            assert other not in ids or ids.index(iliad) < ids.index(other), (mode, body)
        signal = "text_rank" if mode == "text" else "vector_rank" if mode == "vector" else None
        if signal:
            assert all(h[signal] for h in body["hits"]), body
    scoped = api.get(
        f"/documents/{odyssey}/similar", params={"collection": collection()}
    ).raise_for_status()
    assert {h["collection"] for h in scoped.json()["hits"]} == {collection()}
    assert anonymous().get(f"/documents/{odyssey}/similar").status_code == 401


# --- RDF exports -------------------------------------------------------------------------------


def test_document_export_is_rdf_in_the_base_vocabulary(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    need("odyssey")
    doc = docs_by_slug["odyssey"]["id"]
    ttl = Graph().parse(data=streamed(api.get(f"/export/documents/{doc}")), format="turtle")
    node = URIRef(f"urn:semascope:doc:{doc}")
    assert (node, RDF.type, BASE.Document) in ttl
    targets = {str(ttl.value(t, BASE.tagTarget)) for t in ttl.objects(node, BASE.hasTag)}
    assert DEMO + "zeus" in targets, sorted(targets)[:20]
    stages = {
        str(ttl.value(s, URIRef("http://purl.org/dc/terms/title")))
        for s in ttl.subjects(RDF.type, BASE.StageRun)
    }
    assert "tag" in stages
    jsonld = Graph().parse(
        data=streamed(api.get(f"/export/documents/{doc}", params={"format": "jsonld"})),
        format="json-ld",
    )
    assert len(jsonld) == len(ttl)


def test_search_export_streams_the_hits(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    need("odyssey", "iliad")
    body = {"q": "Ulysses", "collection": collection()}
    text = streamed(api.post("/export/search", params={"format": "jsonld"}, json=body))
    g = Graph().parse(data=text, format="json-ld")
    exported = {str(s) for s in g.subjects(RDF.type, BASE.Document)}
    for slug in ("odyssey", "iliad"):
        assert f"urn:semascope:doc:{docs_by_slug[slug]['id']}" in exported
    capped = Graph().parse(
        data=streamed(api.post("/export/search", params={"limit": 1}, json=body)), format="turtle"
    )
    assert len(set(capped.subjects(RDF.type, BASE.Document))) == 1
    # no token: refused (401, or 403 when Django's CSRF check on a session POST answers first)
    assert anonymous().post("/export/search", json=body).status_code in (401, 403)


# --- W3C Web Annotations -----------------------------------------------------------------------


def test_web_annotations_import_export_round_trip(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]], plain_reader: httpx.Client
) -> None:
    need("odyssey")
    doc = api.get(f"/documents/{docs_by_slug['odyssey']['id']}").raise_for_status().json()
    original = "https://annotations.example/it-extras-1"
    payload = {
        "@context": "http://www.w3.org/ns/anno.jsonld",
        "type": "AnnotationPage",
        "items": [
            {
                "id": original,
                "type": "Annotation",
                "creator": {"type": "Person", "name": "Integration Test"},
                "body": [
                    {"type": "TextualBody", "value": NOTE, "purpose": "commenting"},
                    {"type": "TextualBody", "value": "invocation", "purpose": "tagging"},
                ],
                "target": {
                    "source": doc["uri"],
                    "selector": {"type": "TextQuoteSelector", "exact": QUOTE, "suffix": " who"},
                },
            },
            {
                "type": "Annotation",
                "body": {"type": "TextualBody", "value": "x"},
                "target": "https://nowhere.example/doc",
            },
        ],
    }
    params = {"collection": collection()}
    # a user with no role of their own gets the default role (reader at most): no import
    denied = plain_reader.post("/annotations/w3c/import", params=params, json=payload)
    assert denied.status_code == 403, denied.text
    out = api.post("/annotations/w3c/import", params=params, json=payload).raise_for_status().json()
    try:
        assert (out["imported"], out["anchored"], out["unmatched"]) == (1, 1, 1), out
        again = api.post("/annotations/w3c/import", params=params, json=payload).json()
        assert (again["imported"], again["already_imported"]) == (0, 1), again
        page = api.get("/annotations/w3c", params={"document": doc["id"]}).raise_for_status()
        assert page.headers["content-type"].startswith("application/ld+json")
        items = [i for i in page.json()["items"] if i.get("via") == original]
        assert len(items) == 1, page.json()
        note = items[0]
        assert note["creator"] == {"type": "Person", "name": "Integration Test"}
        quote = next(s for s in note["target"]["selector"] if s["type"] == "TextQuoteSelector")
        assert quote["exact"] == QUOTE
        selectors = note["target"]["selector"]
        position = next(s for s in selectors if s["type"] == "TextPositionSelector")
        text = api.get(f"/documents/{doc['id']}/text").json()["text"]
        assert text[position["start"] : position["end"]] == QUOTE
        # the note is in the curation API and the RDF export too
        notes = api.get(f"/curation/documents/{doc['id']}/annotations").json()
        mine = [n for n in notes if n["text"] == NOTE]
        assert mine and mine[0]["tags"] == ["invocation"]
        exported = streamed(api.get(f"/export/documents/{doc['id']}"))
        ttl = Graph().parse(data=exported, format="turtle")
        assert any(
            str(ttl.value(a, OA.via)) == original for a in ttl.subjects(RDF.type, OA.Annotation)
        )
    finally:
        for n in api.get(f"/curation/documents/{doc['id']}/annotations").json():
            if n["text"] == NOTE:
                api.delete(f"/curation/annotations/{n['id']}")
    assert anonymous().get("/annotations/w3c").status_code == 401


# --- OpenSearch and analytics ------------------------------------------------------------------


def test_opensearch_description_and_suggestions(api: httpx.Client, indexed: dict[str, Any]) -> None:
    resp = anonymous().get("/opensearch.xml")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/opensearchdescription+xml")
    ns = {"os": "http://a9.com/-/spec/opensearch/1.1/"}
    root = ET.fromstring(resp.content)  # noqa: S314 - the stack's own output
    html = next(u for u in root.findall("os:Url", ns) if u.get("type") == "text/html")
    assert html.get("template") == base_url() + "/?q={searchTerms}"
    page = httpx.get(base_url() + "/", verify=False, timeout=60.0)  # noqa: S501
    assert 'rel="search"' in page.text and "/api/opensearch.xml" in page.text
    need("odyssey")
    suggestions = api.get("/opensearch/suggest", params={"q": "odys"}).raise_for_status().json()
    assert suggestions[0] == "odys" and any("odyssey" in t.lower() for t in suggestions[1])
    assert anonymous().get("/opensearch/suggest", params={"q": "odys"}).status_code == 401


def test_analytics_overview(api: httpx.Client, indexed: dict[str, Any]) -> None:
    out = api.get("/analytics/overview", params={"collection": collection()}).raise_for_status()
    body = out.json()
    assert body["documents"] == len(demo_texts())
    assert body["top"]["concepts"], body["top"]
    assert body["indexed"] and sum(b["count"] for b in body["indexed"]) == len(demo_texts())
    assert [c["collection"] for c in body["per_collection"]] == [collection()]
    assert body["per_collection"][0]["concepts"]
    assert anonymous().get("/analytics/overview").status_code == 401


# --- oss eval ----------------------------------------------------------------------------------


def test_eval_scores_the_demo_judgements(api: httpx.Client, indexed: dict[str, Any]) -> None:
    from oss_cli import evaluate

    judgements = evaluate.load(ROOT / "packs" / "demo" / "judgements.yaml")
    report = evaluate.evaluate(api, judgements, collection=collection())
    assert report.documents == len(demo_texts())
    scored = len(judgements.queries) - len(report.skipped)
    assert scored >= 5, report.skipped
    for mode in report.modes:
        assert not mode.errors, mode.errors
        assert len(mode.per_query) == scored
        assert mode.mean("ndcg") > 0.5, (mode.mode, mode.mean("ndcg"))
    md = evaluate.markdown(report)
    assert "| hybrid |" in md
    assert ("left out" in md) == bool(report.absent)


def test_oss_eval_runs_in_the_image() -> None:
    proc = oss("eval", "/app/packs/demo/judgements.yaml", "--mode", "keyword", "--no-per-query")
    assert "| keyword |" in proc.stdout
