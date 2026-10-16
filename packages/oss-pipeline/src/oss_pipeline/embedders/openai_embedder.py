"""Any OpenAI-compatible /embeddings endpoint (SEMASCOPE_EMBED_BASE_URL and _API_KEY).

Inputs are sent in batches of `batch_size` (64 by default, `SEMASCOPE_EMBED_BATCH`
overrides it), never all of a document's chunks in one request: endpoints cap the
inputs per request (OpenAI at 2,048 inputs and 300,000 tokens, and local servers
often far lower), and a book has up to 800 chunks of about 470 tokens.
"""

from __future__ import annotations

import os

import httpx

BATCH = 64


class OpenAICompatibleEmbedder:
    name = "openai"

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str = "",
        dimensions: int = 384,
        *,
        batch_size: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model_name = model
        self.dimensions = dimensions
        configured = batch_size or int(os.environ.get("SEMASCOPE_EMBED_BATCH") or BATCH)
        self.batch_size = max(1, configured)
        base = base_url or os.environ.get("SEMASCOPE_EMBED_BASE_URL") or ""
        key = api_key or os.environ.get("SEMASCOPE_EMBED_API_KEY") or ""
        if not base:
            raise ValueError("SEMASCOPE_EMBED_BASE_URL is not set")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = httpx.Client(
            base_url=base.rstrip("/"), headers=headers, timeout=300.0, transport=transport
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            resp = self._client.post("/embeddings", json={"model": self.model_name, "input": batch})
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            if len(data) != len(batch):
                raise ValueError(
                    f"the embedding service returned {len(data)} vectors for {len(batch)} inputs"
                )
            vectors.extend([float(x) for x in d["embedding"]] for d in data)
        if vectors:
            self.dimensions = len(vectors[0])
        return vectors

    def close(self) -> None:
        self._client.close()

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]
