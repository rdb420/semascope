"""The explanation record: what each stage writes on a tag, span pages, and the curated view."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from oss_core.contracts import Span, StageContext, Tag, WorkingDocument
from oss_pipeline.provenance import assign_pages, curated_tags, page_starts
from oss_pipeline.stages import link as link_mod
from oss_pipeline.stages.graph import GraphStage
from oss_pipeline.stages.index import to_solr_document
from oss_pipeline.stages.link import LinkStage
from oss_pipeline.stages.ner import Entity, NerStage
from oss_pipeline.stages.patterns import PatternsStage
from oss_pipeline.stages.structure import StructureStage
from oss_pipeline.stages.tag import TagStage

BASE = "https://semascope.dev/ontology/base#"
PAGES = "Ithaca is an island.\fOdysseus sails home to Ithaca. Mail ody@ithaca.gr."


def _doc() -> WorkingDocument:
    doc = WorkingDocument(
        id="d", collection="texts", source_id="1", uri="file:///x", content_hash="h", text=PAGES
    )
    return StructureStage().run(doc, _ctx())


def _ctx(packs: dict[str, str] | None = None, **settings: Any) -> StageContext:
    return StageContext(
        collection="texts",
        profile="standard",
        settings={"solr_url": "http://solr/solr", **settings},
        pack_versions=packs or {},
    )


def test_structure_records_where_each_page_starts() -> None:
    doc = _doc()
    assert len(doc.pages) == 2
    starts = doc.fields["page_starts"]
    assert starts == page_starts(doc)
    assert doc.text is not None
    assert [doc.text[s : s + 6] for s in starts] == ["Ithaca", "Odysse"]
    # without the stored field the starts are recomputed from the pages
    del doc.fields["page_starts"]
    assert page_starts(doc) == starts


def test_pages_are_assigned_by_offset() -> None:
    doc = _doc()
    second = doc.fields["page_starts"][1]
    tag = Tag(
        iri="x",
        label="x",
        kind="concept",
        stage="tag",
        spans=[Span(start=0, end=6), Span(start=second, end=second + 3), Span(start=1, end=2)],
    )
    tag.spans[2].page = 9  # a page already set is kept
    assign_pages([tag], doc)
    assert [s.page for s in tag.spans] == [1, 2, 9]


def test_old_stored_tags_still_load() -> None:
    stored = {
        "iri": "x",
        "label": "X",
        "kind": "concept",
        "stage": "tag",
        "spans": [{"start": 0, "end": 1}],
    }
    tag = Tag.model_validate(stored)
    assert tag.stage_version is None and tag.linked_from is None and tag.spans[0].page is None


def _tagger(body: dict[str, Any]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))


def test_tag_records_the_dictionary_entry_and_each_span() -> None:
    doc = _doc()
    text = doc.text or ""
    first, second = text.index("Ithaca"), text.rindex("Ithaca")
    entry = {
        "iri": "https://p/ithaca",
        "pref_label": "Ithaca (island)",
        "kind": "individual",
        "pack_id": "p",
        "pack_version": "2",
        "lang": "en",
    }
    body = {
        "response": {
            "docs": [
                {**entry, "id": "alt", "label": "Ithaca", "label_type": "alt"},
                {**entry, "id": "hid", "label": "ithaca", "label_type": "hidden", "lang": "el"},
            ]
        },
        "tags": [
            {"startOffset": second, "endOffset": second + 6, "ids": ["hid"]},
            {"startOffset": first, "endOffset": first + 6, "ids": ["alt"]},
        ],
    }
    out = TagStage(http=_tagger(body)).run(doc, _ctx({"p": "2"}))
    (tag,) = out.tags
    assert tag.stage == "tag" and tag.stage_version == TagStage.version
    # confidence and its explanation come from the best label type seen (alt, not hidden)
    assert (tag.confidence, tag.label_type, tag.matched_label, tag.lang) == (
        0.9,
        "alt",
        "Ithaca",
        "en",
    )
    assert [(s.matched, s.page) for s in tag.spans] == [("ithaca", 2), ("Ithaca", 1)]
    assert (tag.pack_id, tag.pack_version) == ("p", "2")


class _Provider:
    name = "remote"
    model = "http://ner.test"

    def extract(
        self, windows: list[str], language: str | None, labels: dict[str, str]
    ) -> list[list[Entity]]:
        out = []
        for w in windows:
            found = []
            for name in ("Odysseus",):
                at = w.find(name)
                if at >= 0:
                    found.append(Entity("person", name, at, at + len(name), 0.8123))
            out.append(found)
        return out


def test_ner_records_model_version_and_pages() -> None:
    out = NerStage(_Provider()).run(_doc(), _ctx(ner_provider="remote"))
    (tag,) = out.tags
    assert tag.model == "remote:http://ner.test" == out.fields["ner_model"]
    assert tag.stage_version == NerStage.version and tag.confidence == 0.812
    assert [s.page for s in tag.spans] == [2]


def test_patterns_record_version_and_pages() -> None:
    out = PatternsStage().run(_doc(), _ctx(patterns=["emails"]))
    (tag,) = out.tags
    assert tag.stage_version == PatternsStage.version and tag.spans[0].page == 2


def _link_dictionary(monkeypatch: pytest.MonkeyPatch, docs: list[dict[str, Any]]) -> None:
    real = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({"response": {"docs": docs}}).encode())

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    link_mod.clear_cache()


ODYSSEUS = {
    "iri": "https://myth/odysseus",
    "label": "odysseus",
    "label_type": "hidden",
    "pref_label": "Odysseus",
    "pack_id": "myth",
    "pack_version": "3",
}


def test_link_keeps_the_ner_entity_it_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    _link_dictionary(monkeypatch, [ODYSSEUS])
    doc = _doc()
    doc.tags = [
        Tag(
            iri=f"{BASE}Person/odysseus",
            label="Odysseus",
            kind="entity",
            stage="ner",
            stage_version="3",
            confidence=0.62,
            model="spacy:en_core_web_sm",
            spans=[Span(start=0, end=8, page=1)],
        )
    ]
    out = LinkStage().run(doc, _ctx({"myth": "3"}))
    link_mod.clear_cache()
    (tag,) = out.tags
    assert tag.iri == "https://myth/odysseus" and tag.pack_version == "3"
    assert tag.stage == "ner" and tag.confidence == 0.85
    assert (tag.matched_label, tag.label_type) == ("odysseus", "hidden")
    assert tag.linked_from is not None
    assert tag.linked_from.model_dump() == {
        "iri": f"{BASE}Person/odysseus",
        "label": "Odysseus",
        "confidence": 0.62,
        "model": "spacy:en_core_web_sm",
        "link_version": LinkStage.version,
    }


def test_link_into_a_dictionary_tag_keeps_its_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    _link_dictionary(monkeypatch, [ODYSSEUS])
    doc = _doc()
    dictionary = Tag(
        iri="https://myth/odysseus",
        label="Odysseus",
        kind="entity",
        stage="tag",
        label_type="pref",
        pack_id="myth",
        pack_version="3",
        spans=[Span(start=0, end=8)],
    )
    ner = Tag(
        iri=f"{BASE}Person/odysseus",
        label="Odysseus",
        kind="entity",
        stage="ner",
        confidence=0.7,
        spans=[Span(start=20, end=28)],
    )
    doc.tags = [dictionary, ner]
    out = LinkStage().run(doc, _ctx({"myth": "3"}))
    link_mod.clear_cache()
    (tag,) = out.tags
    assert tag.stage == "tag" and tag.label_type == "pref" and len(tag.spans) == 2
    assert tag.linked_from is not None and tag.linked_from.confidence == 0.7


def _tags() -> list[Tag]:
    return [
        Tag(
            iri="https://p#a", label="A", kind="concept", stage="tag", pack_id="p", pack_version="1"
        ),
        Tag(
            iri="https://p#b", label="B", kind="concept", stage="tag", pack_id="p", pack_version="1"
        ),
        Tag(
            iri=f"{BASE}Person/ody",
            label="Ody",
            kind="entity",
            stage="ner",
            confidence=0.6,
            model="spacy:x",
            spans=[Span(start=3, end=6)],
        ),
    ]


def test_curated_view_rejects_links_and_additions() -> None:
    curated = {
        "https://p#b": {"decision": "reject"},
        "https://p#c": {"decision": "accept", "label": "C", "kind": "concept"},
        "https://myth/odysseus": {
            "decision": "accept",
            "linked_from": f"{BASE}Person/ody",
            "label": "Odysseus",
            "kind": "entity",
            "pack_id": "myth",
            "pack_version": "3",
        },
    }
    view = {t.iri: t for t in curated_tags(_tags(), curated)}
    assert sorted(view) == ["https://myth/odysseus", "https://p#a", "https://p#c"]
    linked = view["https://myth/odysseus"]
    assert linked.stage == "curated" and linked.pack_version == "3" and linked.spans
    assert linked.linked_from is not None and linked.linked_from.model == "spacy:x"
    kept = {t.iri for t in curated_tags(_tags(), curated, keep_rejected=True)}
    assert "https://p#b" in kept, "the API keeps a rejected tag (flagged)"
    # rejecting the individual later keeps the link, so the mention stays out too
    curated["https://myth/odysseus"] = {
        "decision": "reject",
        "linked_from": f"{BASE}Person/ody",
    }
    assert {t.iri for t in curated_tags(_tags(), curated)} == {"https://p#a", "https://p#c"}


def test_solr_gets_no_spans_or_provenance() -> None:
    doc = _doc()
    doc.tags = _tags()
    doc.tags[0].matched_label, doc.tags[0].label_type = "a", "alt"
    out = to_solr_document(doc)
    flat = json.dumps(out)
    for word in ("spans", "matched", "label_type", "linked_from", "stage_version", "page_starts"):
        assert word not in flat


class _Graph:
    def __init__(self) -> None:
        self.rows: list[list[dict[str, Any]]] = []
        self.cypher: list[str] = []

    def ensure_constraints(self) -> None:
        return None

    def write(self, cypher: str, rows: list[dict[str, Any]], **_: Any) -> int:
        self.cypher.append(cypher)
        self.rows.append(list(rows))
        return len(rows)


def test_graph_edges_carry_stage_and_pack_versions() -> None:
    graph = _Graph()
    doc = _doc()
    doc.tags = _tags()
    for t in doc.tags:
        t.stage_version = "7"
    doc.tags.append(
        Tag(
            iri="https://myth/odysseus",
            label="Odysseus",
            kind="entity",
            stage="ner",
            stage_version="3",
            pack_id="myth",
            pack_version="4",
        )
    )
    GraphStage(graph).run(doc, _ctx(neo4j_uri="bolt://x"))  # type: ignore[arg-type]
    concepts, entities = graph.rows[1], graph.rows[2]
    assert {r["stage_version"] for r in concepts} == {"7"}
    assert {(r["stage_version"], r["pack_version"]) for r in entities} == {("7", None), ("3", "4")}
    assert "t.stage_version = row.stage_version" in graph.cypher[1]
    assert "m.pack_version = row.pack_version" in graph.cypher[2]
