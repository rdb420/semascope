"""FastEmbed (ONNX, CPU by default) with a small English model baked into the image."""

from __future__ import annotations

import os
from typing import Any


class FastEmbedEmbedder:
    name = "fastembed"

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5") -> None:
        from fastembed import TextEmbedding

        cache = os.environ.get("FASTEMBED_CACHE_PATH") or os.environ.get("SEMASCOPE_MODEL_CACHE")
        kwargs: dict[str, Any] = {"cache_dir": cache} if cache else {}
        providers = os.environ.get("SEMASCOPE_EMBED_PROVIDERS")
        if providers:
            kwargs["providers"] = [p.strip() for p in providers.split(",") if p.strip()]
        self.model_name = model
        self._model = TextEmbedding(model_name=model, **kwargs)
        self.dimensions = (
            384 if "small" in model else int(getattr(self._model, "embedding_size", 384) or 384)
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vec.tolist() for vec in self._model.embed(texts, batch_size=32)]

    def embed_query(self, text: str) -> list[float]:
        first = next(iter(self._model.query_embed([text])))
        return [float(x) for x in first.tolist()]
