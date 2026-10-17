"""Enrichment stage fixes: stale chunks, the events facet, the validate graph, unknown profiles,
remote providers without a base URL, the batched OpenAI embedder, the link dictionary cache,
and stages (with their clients) kept per process."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from oss_core.contracts import StageContext, Tag, WorkingDocument
from oss_pipeline import registry
from oss_pipeline.embedders.openai_embedder import OpenAICompatibleEmbedder
from oss_pipeline.stages import graph as graph_mod
from oss_pipeline.stages import link as link_mod
from oss_pipeline.stages.base import run_stage
from oss_pipeline.stages.embed import EmbedStage
from oss_pipeline.stages.graph import GraphStage
from oss_pipeline.stages.index import to_solr_document
from oss_pipeline.stages.link import LinkStage
from oss_pipeline.stages.ner import NerStage
from oss_pipeline.stages.validate import ValidateStage, document_graph

REPO = Path(__file__).resolve().parents[3]


def _doc(**kw: Any) -> WorkingDocument:
    base: dict[str, Any] = {
        "id": "d",
        "collection": "c",
        "source_id": "1",
        "uri": "file:///x",
        "content_hash": "a" * 64,
    }
    return WorkingDocument(**{**base, **kw})


def _ctx(**settings: Any) -> StageContext:
    return StageContext(
        collection="c",
        profile="default",
        settings={"solr_url": "http://solr/solr", **settings},
        pack_versions={"myth": "1"},
    )


class FakeSolr:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.rows: list[dict[str, Any]] = []

    def delete_by_query(self, collection: str, query: str) -> None:
        self.deleted.append(query)

    def add(self, collection: str, docs: list[dict[str, Any]], **_: Any) -> None:
        self.rows.extend(docs)


# --- embed: stale chunks (SEMA-033) and a missing base URL (O-21) ---------------------------


@pytest.mark.parametrize(
    ("doc", "settings", "reason"),
    [
        (_doc(chunks=["old text"]), {"embed": False}, "embedding disabled"),
        (_doc(chunks=[]), {}, "no chunks"),
    ],
)
def test_a_skipped_embed_stage_deletes_the_old_chunks(
    doc: WorkingDocument, settings: dict[str, Any], reason: str
) -> None:
    solr = FakeSolr()
    out = run_stage(EmbedStage(solr=solr), doc, _ctx(**settings))  # type: ignore[arg-type]
    run = out.stage_runs[-1]
    assert run.outcome == "skipped" and reason in (run.message or "")
    assert solr.deleted == ['document_id:"d"'], "no chunk of an earlier version stays searchable"
    assert solr.rows == []


def test_a_remote_embedder_without_a_base_url_fails_the_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEMASCOPE_EMBED_BASE_URL", raising=False)
    solr = FakeSolr()
    out = run_stage(
        EmbedStage(solr=solr),  # type: ignore[arg-type]
        _doc(chunks=["a passage"]),
        _ctx(embedder="openai", embed_model="text-embedding-3-small", embed_base_url=""),
    )
    run = out.stage_runs[-1]
    assert run.outcome == "failed" and "SEMASCOPE_EMBED_BASE_URL is not set" in (run.message or "")
    assert solr.deleted == [], "a failed stage leaves the previous chunks"
    assert "transient_failures" not in out.fields, "a configuration error is not retried"


# --- OpenAI embedder batches (SEMA-027) ------------------------------------------------------


def test_the_openai_embedder_sends_batches_in_order() -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        sizes.append(len(inputs))
        # answer out of order, as the API allows; `index` says which input each one is
        data = [
            {"index": i, "embedding": [float(text.split()[-1]), 0.5]}
            for i, text in reversed(list(enumerate(inputs)))
        ]
        return httpx.Response(200, json={"data": data})

    e = OpenAICompatibleEmbedder(
        "m", "http://embed.test/v1", transport=httpx.MockTransport(handler)
    )
    vectors = e.embed([f"chunk {i}" for i in range(150)])
    assert sizes == [64, 64, 22], "never more than 64 inputs in one request"
    assert [v[0] for v in vectors] == [float(i) for i in range(150)]
    assert e.dimensions == 2
    small = OpenAICompatibleEmbedder(
        "m", "http://embed.test/v1", batch_size=10, transport=httpx.MockTransport(handler)
    )
    sizes.clear()
    small.embed(["chunk 1"] * 25)
    assert sizes == [10, 10, 5]


def test_the_openai_embedder_refuses_a_short_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1]}]})

    e = OpenAICompatibleEmbedder("m", "http://e.test", transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="1 vectors for 2 inputs"):
        e.embed(["a", "b"])


# --- NER: configuration errors fail, events reach the index (O-21, SEMA-036) -----------------


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"ner_provider": "remote"}, "SEMASCOPE_NER_BASE_URL is not set"),
        ({"ner_provider": "remot"}, "unknown NER provider 'remot'"),
    ],
)
def test_a_misconfigured_ner_provider_fails_the_stage(
    settings: dict[str, Any], message: str
) -> None:
    doc = _doc(text="Odysseus came home.", language="en")
    out = run_stage(NerStage(), doc, _ctx(**settings))  # type: ignore[arg-type]
    run = out.stage_runs[-1]
    assert run.outcome == "failed" and message in (run.message or ""), run


def test_ner_events_reach_the_events_facet() -> None:
    doc = _doc(text="x")
    doc.fields.update({"events": ["World War II"], "places": ["Ithaca"]})
    out = to_solr_document(doc)
    assert out["events_ss"] == ["World War II"] and out["places_ss"] == ["Ithaca"]


# --- validate: rdflib terms (SEMA-058) -------------------------------------------------------


def test_validate_survives_carriage_returns_and_odd_iris() -> None:
    from rdflib import Graph, URIRef

    shapes = (REPO / "packs" / "base" / "shapes.ttl").read_text()
    doc = _doc(title='Line one\r\nline "two" \\ three', language="en")
    doc.tags = [
        Tag(
            iri="https://semascope.dev/ontology/base#Person/a>b c",
            label="A>b c",
            kind="entity",
            stage="ner",
            confidence=0.7,
        ),  # fmt: skip
        Tag(iri="https://p/themes#flood", label="Flood", kind="concept", stage="tag"),
    ]
    out = run_stage(ValidateStage(), doc, _ctx(shapes_ttl=shapes))  # type: ignore[arg-type]
    assert out.stage_runs[-1].outcome == "ok", out.stage_runs[-1].message
    assert out.fields["shacl_conforms"] is True, out.fields["shacl_violations"]
    # the Turtle is valid and keeps the title whole; the odd IRI stays one term
    g = Graph().parse(data=document_graph(doc), format="turtle")
    titles = [str(o) for o in g.objects(None, URIRef("http://purl.org/dc/terms/title"))]
    assert titles == ['Line one\r\nline "two" \\ three']
    targets = {
        str(o) for o in g.objects(None, URIRef("https://semascope.dev/ontology/base#tagTarget"))
    }
    assert "https://semascope.dev/ontology/base#Person/a%3Eb%20c" in targets
    assert len(list(g.subjects(None, URIRef("https://semascope.dev/ontology/base#Tag")))) == 2


# --- profiles (SEMA-062) and stages per process (SEMA-037) -----------------------------------


def test_an_unknown_profile_raises() -> None:
    with pytest.raises(registry.UnknownProfileError, match="unknown profile 'defualt'"):
        registry.profile("defualt")


def test_stages_are_built_once_per_process() -> None:
    registry.close_stages()
    first = registry.profile("default")
    again = registry.profile("full")
    assert {s.name: id(s) for s in first} == {
        s.name: id(s) for s in again if s.name in {f.name for f in first}
    }, "the same instances for every task of the process"
    # a forked pool process: the parent's instances (and their connections) are not reused
    registry._process["pid"] = -1  # noqa: SLF001
    forked = registry.profile("default")
    assert all(a is not b for a, b in zip(first, forked, strict=True))
    registry.close_stages()


class FakeGraphClient:
    built: list[FakeGraphClient] = []

    def __init__(self, uri: str, user: str, password: str) -> None:
        self.uri = uri
        self.constraints = 0
        self.closed = False
        self.writes = 0
        FakeGraphClient.built.append(self)

    def ensure_constraints(self) -> None:
        self.constraints += 1

    def write(self, cypher: str, rows: Any, *, batch: int = 500) -> int:
        self.writes += 1
        return 0

    def close(self) -> None:
        self.closed = True


def test_the_graph_stage_keeps_one_driver_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeGraphClient.built.clear()
    monkeypatch.setattr(graph_mod, "GraphClient", FakeGraphClient)
    stage = GraphStage()
    settings = {"neo4j_uri": "bolt://neo4j:7687", "neo4j_password": "pw"}
    for i in range(5):
        stage.run(_doc(id=f"d{i}"), _ctx(**settings))
    assert len(FakeGraphClient.built) == 1, "one driver for every document"
    assert FakeGraphClient.built[0].constraints == 1, "constraints once"
    stage.run(_doc(), _ctx(**{**settings, "neo4j_uri": "bolt://other:7687"}))
    assert len(FakeGraphClient.built) == 2 and FakeGraphClient.built[0].closed
    assert FakeGraphClient.built[1].constraints == 1
    stage.close()
    assert FakeGraphClient.built[1].closed


def test_stage_clients_are_reused_and_rebuilt_when_settings_change() -> None:
    stage = EmbedStage()
    first = stage._client(_ctx())  # noqa: SLF001
    assert stage._client(_ctx()) is first  # noqa: SLF001
    other = stage._client(_ctx(solr_url="http://solr2/solr"))  # noqa: SLF001
    assert other is not first and first._http.is_closed  # noqa: SLF001
    stage.close()
    assert other._http.is_closed  # noqa: SLF001


# --- link: the dictionary cache follows the dictionary (SEMA-056, SEMA-024) -----------------


class FakeEntities:
    """Solr `entities` for the link stage: a fingerprint request (rows=0) and pages."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.version = 1
        self.fetches = 0
        self.fingerprints = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.params.get("rows") == "0":
            self.fingerprints += 1
            body = {"response": {"numFound": len(self.docs)}, "facets": {"newest": self.version}}
            return httpx.Response(200, json=body)
        self.fetches += 1
        return httpx.Response(200, json={"response": {"docs": self.docs}})


def _entry(label: str, iri: str) -> dict[str, Any]:
    return {
        "iri": iri,
        "label": label,
        "label_type": "pref",
        "pref_label": label,
        "pack_id": "myth",
        "pack_version": "1",
    }


def _ner_doc(label: str) -> WorkingDocument:
    doc = _doc(text=label)
    doc.tags = [Tag(iri=f"base#Person/{label.lower()}", label=label, kind="entity", stage="ner")]
    return doc


def test_the_link_dictionary_is_fetched_again_when_it_changes() -> None:
    link_mod.clear_cache()
    solr = FakeEntities([_entry("Odysseus", "https://myth/odysseus")])
    stage = LinkStage(http=httpx.Client(transport=httpx.MockTransport(solr)))
    out = stage.run(_ner_doc("Odysseus"), _ctx())
    assert out.tags[0].iri == "https://myth/odysseus"
    stage.run(_ner_doc("Odysseus"), _ctx())
    assert (solr.fetches, solr.fingerprints) == (1, 2), "unchanged: the cached dictionary"
    # a curator's draft label for this collection arrives (same pack version, no restart)
    solr.docs.append(_entry("Ulysses", "https://myth/odysseus"))
    solr.version = 2
    out = stage.run(_ner_doc("Ulysses"), _ctx())
    assert out.tags[0].iri == "https://myth/odysseus", "the new label links at once"
    assert solr.fetches == 2
    # a re-sync of the same version replaces an entry: same count, newer _version_
    solr.docs[1] = _entry("Ulysses", "https://myth/ulysses")
    solr.version = 3
    out = stage.run(_ner_doc("Ulysses"), _ctx())
    assert out.tags[0].iri == "https://myth/ulysses" and solr.fetches == 3
    link_mod.clear_cache()


def test_link_asks_nothing_when_there_is_nothing_to_link() -> None:
    solr = FakeEntities([])
    stage = LinkStage(http=httpx.Client(transport=httpx.MockTransport(solr)))
    out = stage.run(_doc(text="x"), _ctx())
    assert (solr.fetches, solr.fingerprints) == (0, 0) and out.fields["linked_count"] == 0
