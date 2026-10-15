"""Embedders: text to dense vectors, behind the Embedder protocol."""

from __future__ import annotations

from functools import lru_cache

from oss_core.contracts import Embedder


@lru_cache(maxsize=4)
def get_embedder(name: str = "fastembed", model: str = "BAAI/bge-small-en-v1.5") -> Embedder:
    if name == "fastembed":
        from oss_pipeline.embedders.fastembed_embedder import FastEmbedEmbedder

        return FastEmbedEmbedder(model)
    if name == "openai":
        from oss_pipeline.embedders.openai_embedder import OpenAICompatibleEmbedder

        return OpenAICompatibleEmbedder(model)
    raise ValueError(f"unknown embedder {name}")
