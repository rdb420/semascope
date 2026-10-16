"""`oss doctor` reports whether the embedding model fits the chunks schema."""

from __future__ import annotations

from typing import Any

import httpx

from oss_cli.vectors import vector_report
from oss_core.config import Settings

SCHEMA_RC2 = {
    "schema": {
        "fieldTypes": [
            {"name": "knn_vector_384", "class": "solr.DenseVectorField", "vectorDimension": 384}
        ],
        "fields": [{"name": "vector", "type": "knn_vector_384"}],
    }
}


def _client(schema: dict[str, Any], models: list[Any], dims: int = 768) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/chunks/schema"):
            return httpx.Response(200, json=schema)
        if path.endswith("/chunks/select"):
            return httpx.Response(200, json={"facet_counts": {"facet_fields": {"model_s": models}}})
        if path.endswith("/embed/dense"):
            return httpx.Response(200, json={"vectors": [[0.1] * dims]})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_bundled_model_fits() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    status, ok, notes = vector_report(settings, _client(SCHEMA_RC2, ["BAAI/bge-small-en-v1.5", 10]))
    assert ok and "384 dimensions -> field vector" in status and notes == []


def test_a_768_dimension_sidecar_on_an_rc2_schema_is_a_failure() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        semascope_embedder="sidecar",
        semascope_embed_model="BAAI/bge-base-en-v1.5",
        semascope_embed_base_url="http://embed.test",
    )
    status, ok, notes = vector_report(
        settings, _client(SCHEMA_RC2, ["BAAI/bge-small-en-v1.5", 10], dims=768)
    )
    assert not ok and "no field for 768-dimension" in status and "asked the sidecar" in status
    assert notes and "BAAI/bge-small-en-v1.5 (10)" in notes[0] and "--stage embed" in notes[0]


def test_an_unknown_model_is_reported_not_failed() -> None:
    settings = Settings(_env_file=None, semascope_embed_model="me/custom")  # type: ignore[call-arg]
    status, ok, _ = vector_report(settings, _client(SCHEMA_RC2, []))
    assert ok and "SEMASCOPE_EMBED_DIMENSIONS" in status
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, semascope_embed_model="me/custom", semascope_embed_dimensions=1024
    )
    status, ok, _ = vector_report(settings, _client(SCHEMA_RC2, []))
    assert not ok and "vector_1024" in status
