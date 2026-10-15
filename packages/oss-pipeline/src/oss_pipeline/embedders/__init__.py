"""Embedders: text to dense vectors, behind the Embedder protocol.

`fastembed` runs in-process (CPU, or CUDA in the gpu image); `openai` and
`sidecar` call a service. One instance per (provider, model, endpoint) lives
for the life of the process.
"""

from __future__ import annotations

from functools import lru_cache

from oss_core.contracts import Embedder


@lru_cache(maxsize=4)
def get_embedder(
    name: str = "fastembed",
    model: str = "BAAI/bge-small-en-v1.5",
    base_url: str | None = None,
    api_key: str = "",
) -> Embedder:
    if name == "fastembed":
        from oss_pipeline.embedders.fastembed_embedder import FastEmbedEmbedder

        return FastEmbedEmbedder(model)
    if name == "openai":
        from oss_pipeline.embedders.openai_embedder import OpenAICompatibleEmbedder

        return OpenAICompatibleEmbedder(model, base_url, api_key)
    if name == "sidecar":
        from oss_pipeline.embedders.sidecar_embedder import SidecarEmbedder

        return SidecarEmbedder(model, base_url, api_key)
    raise ValueError(f"unknown embedder {name}")
