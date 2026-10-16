"""Chunk vector fields: one Solr field per embedding dimension.

A Solr DenseVectorField has a fixed dimension, so the `chunks` configset
declares one field per supported dimension and every chunk fills the field
of its model's dimension: `vector` for 384 (the bundled bge-small, and the
only field in 1.0.0-rc.2), `vector_<n>` for the others. Changing to a model
of another dimension needs no schema change, only a re-embed
(`oss reindex --stage embed`). Keep `DIMENSIONS` in sync with
`deploy/solr/configsets/chunks/conf/managed-schema.xml`.
"""

from __future__ import annotations

from typing import Any

DIMENSIONS = (256, 384, 512, 768, 1024, 1536, 2560, 3072, 4096)
LEGACY_DIMENSION = 384  # its field keeps the rc.2 name, so existing indexes stay searchable

# Dimensions of common models, for `oss doctor` when the model is not asked directly.
# Keys are lower-case and matched against the end of the configured model name.
KNOWN_MODELS = {
    "bge-small-en-v1.5": 384,
    "bge-base-en-v1.5": 768,
    "bge-large-en-v1.5": 1024,
    "bge-m3": 1024,
    "all-minilm-l6-v2": 384,
    "paraphrase-multilingual-minilm-l12-v2": 384,
    "multilingual-e5-small": 384,
    "multilingual-e5-base": 768,
    "multilingual-e5-large": 1024,
    "nomic-embed-text-v1.5": 768,
    "nomic-embed-text": 768,
    "mxbai-embed-large-v1": 1024,
    "snowflake-arctic-embed-m": 768,
    "jina-embeddings-v2-base-en": 768,
    "jina-embeddings-v3": 1024,
    "qwen3-embedding-0.6b": 1024,
    "qwen3-embedding-4b": 2560,
    "qwen3-embedding-8b": 4096,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class UnsupportedDimensionError(ValueError):
    """No chunks field exists for vectors of this length."""


def vector_field(dimensions: int) -> str:
    """The `chunks` field for vectors of this dimension."""

    if dimensions not in DIMENSIONS:
        raise UnsupportedDimensionError(
            f"no chunks vector field for {dimensions}-dimension embeddings; "
            f"supported: {', '.join(map(str, DIMENSIONS))}"
        )
    return "vector" if dimensions == LEGACY_DIMENSION else f"vector_{dimensions}"


def known_dimensions(model: str) -> int | None:
    """The dimension of a well-known embedding model, or None."""

    name = model.strip().lower()
    for key in sorted(KNOWN_MODELS, key=len, reverse=True):
        if name == key or name.endswith("/" + key) or name.endswith(key):
            return KNOWN_MODELS[key]
    return None


def schema_vector_fields(schema: dict[str, Any]) -> dict[str, int]:
    """Field name to dimension for every dense vector field of a Solr Schema API response."""

    body = schema.get("schema", schema)
    dims: dict[str, int] = {}
    for ft in body.get("fieldTypes", []):
        if str(ft.get("class", "")).endswith("DenseVectorField") and ft.get("vectorDimension"):
            dims[str(ft["name"])] = int(ft["vectorDimension"])
    return {
        str(f["name"]): dims[str(f["type"])]
        for f in body.get("fields", [])
        if str(f.get("type")) in dims
    }


def check_vectors(dimensions: int | None, fields: dict[str, int]) -> tuple[bool, str]:
    """Whether the configured model's vectors fit the `chunks` schema, and why not."""

    if dimensions is None:
        return True, "model dimension unknown; set SEMASCOPE_EMBED_DIMENSIONS to check it"
    try:
        field = vector_field(dimensions)
    except UnsupportedDimensionError as exc:
        return False, str(exc)
    if fields.get(field) == dimensions:
        return True, f"{dimensions} dimensions -> field {field}"
    if field in fields:
        return False, f"field {field} has {fields[field]} dimensions, the model {dimensions}"
    return False, (
        f"the chunks schema has no field for {dimensions}-dimension vectors ({field}); "
        "restart the server so it uploads the current configset, then "
        "`./oss reindex --collection <slug> --stage embed`"
    )
