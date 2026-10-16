"""The chunk vector field follows the model's dimension, and the configset declares them all."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405 - our own configset
from pathlib import Path

import pytest

from oss_core.vectors import (
    DIMENSIONS,
    UnsupportedDimensionError,
    check_vectors,
    known_dimensions,
    schema_vector_fields,
    vector_field,
)

SCHEMA = (
    Path(__file__).resolve().parents[3] / "deploy/solr/configsets/chunks/conf/managed-schema.xml"
)


def test_field_per_dimension_keeps_the_rc2_name_for_384() -> None:
    assert vector_field(384) == "vector"
    assert vector_field(768) == "vector_768" and vector_field(4096) == "vector_4096"
    with pytest.raises(UnsupportedDimensionError, match="supported: 256, 384"):
        vector_field(100)


def test_the_chunks_configset_declares_every_dimension() -> None:
    root = ET.parse(SCHEMA).getroot()  # noqa: S314
    types = {
        t.get("name"): int(t.get("vectorDimension") or 0)
        for t in root.iter("fieldType")
        if t.get("class") == "solr.DenseVectorField"
    }
    fields = {
        f.get("name"): types[f.get("type")] for f in root.iter("field") if f.get("type") in types
    }
    assert fields == {vector_field(d): d for d in DIMENSIONS}


def test_known_models() -> None:
    assert known_dimensions("BAAI/bge-small-en-v1.5") == 384
    assert known_dimensions("BAAI/bge-base-en-v1.5") == 768
    assert known_dimensions("text-embedding-3-large") == 3072
    assert known_dimensions("nomic-ai/nomic-embed-text-v1.5") == 768
    assert known_dimensions("someone/private-model") is None


def test_schema_api_fields_and_the_check() -> None:
    schema = {
        "schema": {
            "fieldTypes": [
                {
                    "name": "knn_vector_384",
                    "class": "solr.DenseVectorField",
                    "vectorDimension": 384,
                },
                {"name": "string", "class": "solr.StrField"},
            ],
            "fields": [
                {"name": "vector", "type": "knn_vector_384"},
                {"name": "id", "type": "string"},
            ],
        }
    }
    fields = schema_vector_fields(schema)
    assert fields == {"vector": 384}
    assert check_vectors(384, fields)[0]
    ok, message = check_vectors(768, fields)
    assert not ok and "no field for 768-dimension" in message and "--stage embed" in message
    ok, message = check_vectors(100, fields)
    assert not ok and "supported" in message
    assert check_vectors(None, fields)[0], "an unknown dimension is a warning, not a failure"
