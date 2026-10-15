"""Any OpenAI-compatible /embeddings endpoint.

Configured by SEMASCOPE_EMBED_BASE_URL and SEMASCOPE_EMBED_API_KEY.
"""

from __future__ import annotations

import os

import httpx


class OpenAICompatibleEmbedder:
    name = "openai"

    def __init__(self, model: str, dimensions: int = 384) -> None:
        self.model_name = model
        self.dimensions = dimensions
        self._base = (os.environ.get("SEMASCOPE_EMBED_BASE_URL") or "").rstrip("/")
        self._key = os.environ.get("SEMASCOPE_EMBED_API_KEY") or ""
        if not self._base:
            raise ValueError("SEMASCOPE_EMBED_BASE_URL is not set")

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        resp = httpx.post(
            f"{self._base}/embeddings",
            json={"model": self.model_name, "input": texts},
            headers=headers,
            timeout=120.0,
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]
