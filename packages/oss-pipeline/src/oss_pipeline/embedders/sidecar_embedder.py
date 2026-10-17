"""An embedding sidecar: `POST {base}/embed/dense {"texts": [...]}` answering `{"vectors": [...]}`.

This is the contract of the open-source `embedding-sidecar` (FastEmbed behind
FastAPI); any service with the same two fields works. The model name reported
by the service is recorded on every chunk so an index never mixes models.
"""

from __future__ import annotations

import httpx

from oss_pipeline.connectors.guard import service_client


class SidecarEmbedder:
    name = "sidecar"

    def __init__(
        self,
        model: str,
        base_url: str | None,
        api_key: str = "",
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("SEMASCOPE_EMBED_BASE_URL is not set")
        self.model_name = model
        self.dimensions = 384
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = service_client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=300.0, transport=transport
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            resp = self._client.post("/embed/dense", json={"texts": texts[i : i + 32]})
            resp.raise_for_status()
            body = resp.json()
            if body.get("model"):
                self.model_name = str(body["model"])
            vectors = [[float(x) for x in v] for v in body["vectors"]]
            if vectors:
                self.dimensions = len(vectors[0])
            out.extend(vectors)
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]
