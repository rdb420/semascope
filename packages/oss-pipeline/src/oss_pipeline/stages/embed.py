"""embed: dense vectors for the document's chunks, written to the Solr `chunks` collection.

Chunks come from the structure stage. Old chunks of the document are deleted
first, so a reindex replaces them. The embedding input is the title and the
chunk text, which is also what query embeddings are compared against. Each
vector goes to the field of its dimension (`oss_core.vectors.vector_field`); a
model whose dimension has no field fails the stage before anything is deleted.
"""

from __future__ import annotations

from oss_core.contracts import StageContext, WorkingDocument
from oss_core.vectors import vector_field
from oss_pipeline.clients.solr import SolrClient
from oss_pipeline.embedders import get_embedder
from oss_pipeline.stages.base import SkipStageError


class EmbedStage:
    name = "embed"
    version = "1"
    queue = "cpu"
    requires = ("chunks",)
    provides = ("embedded",)

    def __init__(self, solr: SolrClient | None = None) -> None:
        self._solr = solr

    def _client(self, ctx: StageContext) -> SolrClient:
        if self._solr is None:
            self._solr = SolrClient(
                str(ctx.settings["solr_url"]),
                user=ctx.settings.get("solr_user"),
                password=ctx.settings.get("solr_password"),
            )
        return self._solr

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        if not ctx.settings.get("embed", True):
            raise SkipStageError("embedding disabled for this collection")
        if not doc.chunks:
            raise SkipStageError("no chunks")
        max_chunks = int(ctx.settings.get("embed_max_chunks", 800))
        chunks = doc.chunks[:max_chunks]
        pages = list(doc.fields.get("chunk_pages") or [])
        try:
            embedder = get_embedder(
                str(ctx.settings.get("embedder", "fastembed")),
                str(ctx.settings.get("embed_model", "BAAI/bge-small-en-v1.5")),
                ctx.settings.get("embed_base_url") or None,
                str(ctx.settings.get("embed_api_key") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - model missing or endpoint unset
            raise SkipStageError(f"embedder unavailable: {exc}") from exc
        title = (doc.title or "").strip()
        inputs = [f"{title}\n\n{chunk}" if title else chunk for chunk in chunks]
        vectors = embedder.embed(inputs)
        field = vector_field(len(vectors[0])) if vectors else "vector"
        collection = str(ctx.settings.get("solr_chunks", "chunks"))
        client = self._client(ctx)
        client.delete_by_query(collection, f'document_id:"{doc.id}"')
        rows = [
            {
                "id": f"{doc.id}:{i}",
                "document_id": doc.id,
                "collection_s": doc.collection,
                "position_i": i,
                "page_i": pages[i] if i < len(pages) else None,
                "text_txt": chunk,
                field: [round(x, 6) for x in vec],
                "model_s": getattr(embedder, "model_name", "unknown"),
            }
            for i, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True))
        ]
        for i in range(0, len(rows), 200):
            client.add(collection, rows[i : i + 200], commit_within_ms=3000)
        doc.embeddings["chunks"] = []  # vectors live in Solr; keep the record small
        doc.fields["embedded"] = {
            "chunks": len(rows),
            "model": getattr(embedder, "model_name", "unknown"),
        }
        return doc
