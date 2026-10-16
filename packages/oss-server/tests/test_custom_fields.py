"""A source's custom `x_` fields never replace the index's own fields.

A connector (a table column, an RDF subject, a plugin) may hand the pipeline
custom fields named `x_<name>`, indexed as `<name>`. A name that is one of
the index's own fields (`id`, `collection_s`, `uri_s`, the text, the tags) is
dropped, so a source cannot make its document replace another collection's.
"""

from __future__ import annotations

from typing import Any

import pytest

from oss_core.contracts import FetchedItem, SourceItem, WorkingDocument
from oss_pipeline.stages.index import custom_fields, to_solr_document
from oss_server.catalogue.models import Collection, Document, Source, document_id
from oss_server.pipeline import tasks

HOSTILE = {
    "x_id": "b" * 32,
    "x_collection_s": "other",
    "x_source_id_s": "999",
    "x_uri_s": "file:///other/secret.txt",
    "x_text_txt": "replaced",
    "x_title_txt": "replaced",
    "x_concept_iris_ss": ["https://x/forged"],
    "x__version_": 1,
    "x_owner_s} OR x": "bad name",
    "X_Owner_s": "not an x_ key",
    "x_owner_s": "ops",  # an ordinary custom field: kept
    "x_demo-places_paths": ["places/ithaca"],  # scheme slugs may hold hyphens: kept
}


class Connector:
    def fetch(self, item: SourceItem) -> FetchedItem:
        item.metadata["fields"] = dict(HOSTILE)
        return FetchedItem(item=item, content=b"text", media_type="text/plain")


def test_the_index_stage_keeps_its_own_fields() -> None:
    doc = WorkingDocument(
        id="a" * 32, collection="mine", source_id="1", uri="file:///mine.txt", content_hash="h"
    )
    doc.text = "own text"
    doc.fields.update(HOSTILE)
    out = to_solr_document(doc)
    assert out["id"] == "a" * 32 and out["collection_s"] == "mine"
    assert out["source_id_s"] == "1" and out["uri_s"] == "file:///mine.txt"
    assert out["text_txt"] == "own text" and out["title_txt"] == "mine.txt"
    assert "concept_iris_ss" not in out and "_version_" not in out
    assert out["owner_s"] == "ops"
    assert out["demo-places_paths"] == ["places", "places/ithaca"]
    assert not [k for k in out if "}" in k or k.lower().startswith("owner_s ")]


def test_a_connector_cannot_overwrite_another_collections_document(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = Collection.objects.create(slug="other", name="Other")
    mine = Collection.objects.create(slug="mine", name="Mine")
    theirs = Source.objects.create(collection=other, name="s", connector="filesystem")
    Document.objects.create(
        id="b" * 32, collection=other, source=theirs, uri="file:///other/secret.txt"
    )
    source = Source.objects.create(collection=mine, name="evil", connector="evil")
    uri = "plugin://evil/1"
    doc_id = document_id("mine", uri)
    Document.objects.create(id=doc_id, collection=mine, source=source, uri=uri)
    seen: list[WorkingDocument] = []

    def run_profile(stages: Any, work: WorkingDocument, ctx: Any, **_: Any) -> WorkingDocument:
        seen.append(work)
        return work

    monkeypatch.setattr(tasks, "connectors", lambda: {"evil": Connector()})
    monkeypatch.setattr(tasks, "run_profile", run_profile)
    tasks.process_document(doc_id, SourceItem(uri=uri).model_dump(mode="json"))

    (work,) = seen
    assert set(work.fields) == {"x_owner_s", "x_demo-places_paths"}
    solr = to_solr_document(work)
    assert solr["id"] == doc_id != "b" * 32
    assert solr["collection_s"] == "mine" and solr["uri_s"] == uri


def test_custom_fields_filter() -> None:
    assert custom_fields(HOSTILE) == {
        "x_owner_s": "ops",
        "x_demo-places_paths": ["places/ithaca"],
    }
