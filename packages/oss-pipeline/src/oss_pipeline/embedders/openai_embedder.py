"""Any OpenAI-compatible /embeddings endpoint (SEMASCOPE_EMBED_BASE_URL and _API_KEY)."""

from __future__ import annotations

import os

import httpx


class OpenAICompatibleEmbedder:
    name = "openai"

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str = "",
        dimensions: int = 384,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model_name = model
        self.dimensions = dimensions
        base = base_url or os.environ.get("SEMASCOPE_EMBED_BASE_URL") or ""
        key = api_key or os.environ.get("SEMASCOPE_EMBED_API_KEY") or ""
        if not base:
            raise ValueError("SEMASCOPE_EMBED_BASE_URL is not set")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = httpx.Client(
            base_url=base.rstrip("/"), headers=headers, timeout=300.0, transport=transport
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post("/embeddings", json={"model": self.model_name, "input": texts})
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        vectors = [[float(x) for x in d["embedding"]] for d in data]
        if vectors:
            self.dimensions = len(vectors[0])
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]
