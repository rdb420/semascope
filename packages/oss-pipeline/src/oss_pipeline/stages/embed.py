"""embed: dense vectors for the document's chunks, written to the Solr `chunks` collection.

Chunks come from the structure stage. Old chunks of the document are deleted
before the new ones are added, so a reindex replaces them. The embedding input
is the title and the chunk text, which is also what query embeddings are
compared against. Each vector goes to the field of its dimension
(`oss_core.vectors.vector_field`).

A skipped stage leaves the document with no chunks: when embedding is turned off
for the collection, or the document has no text to chunk, its old chunks are
deleted before the stage skips, so no passage of an earlier version stays
searchable. A failed stage leaves the previous chunks as they were (the failure
is on the document and is retried): an embedder that cannot be built (an
unknown provider, a remote provider with no base URL), a model whose dimension
has no field, or an embedding service that is down.
"""

from __future__ import annotations

from oss_core.contracts import StageContext, WorkingDocument
from oss_core.vectors import vector_field
from oss_pipeline.clients.solr import SolrClient, quote_term
from oss_pipeline.embedders import get_embedder
from oss_pipeline.stages.base import SkipStageError, solr_key


class EmbedStage:
    name = "embed"
    version = "1"
    requires = ("chunks",)
    provides = ("embedded",)

    def __init__(self, solr: SolrClient | None = None) -> None:
        self._solr = solr
        self._solr_key: tuple[str, ...] | None = None  # None: a client handed in

    def _client(self, ctx: StageContext) -> SolrClient:
        key = solr_key(ctx)
        if self._solr is None or (self._solr_key is not None and self._solr_key != key):
            self.close()
            self._solr = SolrClient(
                str(ctx.settings["solr_url"]),
                user=ctx.settings.get("solr_user"),
                password=ctx.settings.get("solr_password"),
            )
            self._solr_key = key
        return self._solr

    def close(self) -> None:
        if self._solr is not None and self._solr_key is not None:
            self._solr.close()  # only a client this stage built
            self._solr = None

    def _clear(self, doc: WorkingDocument, ctx: StageContext) -> None:
        collection = str(ctx.settings.get("solr_chunks", "chunks"))
        self._client(ctx).delete_by_query(collection, f"document_id:{quote_term(doc.id)}")

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        if not ctx.settings.get("embed", True):
            self._clear(doc, ctx)
            raise SkipStageError("embedding disabled for this collection")
        if not doc.chunks:
            self._clear(doc, ctx)
            raise SkipStageError("no chunks")
        max_chunks = int(ctx.settings.get("embed_max_chunks", 800))
        chunks = doc.chunks[:max_chunks]
        pages = list(doc.fields.get("chunk_pages") or [])
        # an embedder that cannot be built (unknown provider, remote with no base URL) is a
        # configuration error: the stage fails, visibly, instead of skipping
        embedder = get_embedder(
            str(ctx.settings.get("embedder", "fastembed")),
            str(ctx.settings.get("embed_model", "BAAI/bge-small-en-v1.5")),
            ctx.settings.get("embed_base_url") or None,
            str(ctx.settings.get("embed_api_key") or ""),
        )
        title = (doc.title or "").strip()
        inputs = [f"{title}\n\n{chunk}" if title else chunk for chunk in chunks]
        vectors = embedder.embed(inputs)
        field = vector_field(len(vectors[0])) if vectors else "vector"
        collection = str(ctx.settings.get("solr_chunks", "chunks"))
        client = self._client(ctx)
        client.delete_by_query(collection, f"document_id:{quote_term(doc.id)}")
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
