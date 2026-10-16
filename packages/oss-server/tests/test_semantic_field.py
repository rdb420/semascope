"""Semantic search queries the vector field of the current model's dimension."""

from __future__ import annotations

from typing import Any

import pytest
from ninja.errors import HttpError

from oss_server.api import search
from oss_server.api.schemas import SearchIn


class FakeEmbedder:
    model_name = "BAAI/bge-base-en-v1.5"

    def __init__(self, dims: int) -> None:
        self.dims = dims

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * self.dims


class FakeSolr:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.params = params
        return {"response": {"docs": []}}


@pytest.mark.parametrize(("dims", "field"), [(384, "vector"), (768, "vector_768")])
def test_knn_uses_the_models_field(dims: int, field: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search, "query_embedder", lambda: FakeEmbedder(dims))
    solr = FakeSolr()
    search._knn_chunks(solr, SearchIn(q="ithaca"), 10)  # type: ignore[arg-type]
    assert solr.params["q"].startswith("{!knn f=" + field + " topK=10}[")
    assert 'model_s:"BAAI/bge-base-en-v1.5"' in solr.params["fq"]


def test_an_unsupported_dimension_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search, "query_embedder", lambda: FakeEmbedder(100))
    with pytest.raises(HttpError, match="100-dimension"):
        search._knn_chunks(FakeSolr(), SearchIn(q="x"), 10)  # type: ignore[arg-type]
