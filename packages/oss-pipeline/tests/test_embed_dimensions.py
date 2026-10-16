"""The embed stage writes each vector to the field of its dimension."""

from __future__ import annotations

from typing import Any

import pytest

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.stages import embed as embed_mod
from oss_pipeline.stages.base import run_stage
from oss_pipeline.stages.embed import EmbedStage


class FakeSolr:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    def delete_by_query(self, collection: str, query: str) -> None:
        self.deleted.append(query)

    def add(self, collection: str, docs: list[dict[str, Any]], **_: Any) -> None:
        self.rows.extend(docs)


class FakeEmbedder:
    model_name = "test/model"

    def __init__(self, dims: int) -> None:
        self.dims = dims

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.5] * self.dims for _ in texts]


def _run(dims: int, monkeypatch: pytest.MonkeyPatch) -> tuple[WorkingDocument, FakeSolr]:
    monkeypatch.setattr(embed_mod, "get_embedder", lambda *a, **k: FakeEmbedder(dims))
    solr = FakeSolr()
    doc = WorkingDocument(
        id="d", collection="c", source_id="1", uri="u", content_hash="h", chunks=["one", "two"]
    )
    ctx = StageContext(collection="c", profile="p", settings={"solr_url": "http://solr/solr"})
    return run_stage(EmbedStage(solr=solr), doc, ctx), solr  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("dims", "field"), [(384, "vector"), (768, "vector_768"), (1536, "vector_1536")]
)
def test_vectors_go_to_the_field_of_their_dimension(
    dims: int, field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, solr = _run(dims, monkeypatch)
    assert out.stage_runs[-1].outcome == "ok"
    assert [sorted(k for k in r if k.startswith("vector")) for r in solr.rows] == [[field]] * 2
    assert len(solr.rows[0][field]) == dims


def test_an_unsupported_dimension_fails_before_deleting_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out, solr = _run(100, monkeypatch)
    run = out.stage_runs[-1]
    assert run.outcome == "failed" and "no chunks vector field for 100-dimension" in (
        run.message or ""
    )
    assert solr.deleted == [] and solr.rows == []
