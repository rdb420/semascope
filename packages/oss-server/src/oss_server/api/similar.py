"""More like this: the documents most similar to a given one (parity row FUZZ-12).

Two signals, fused by reciprocal rank as hybrid search fuses keyword and vector:

- **text**: Solr's MoreLikeThis query parser (`{!mlt}`, which works in SolrCloud)
  over the document's title and text. It picks the document's most distinctive
  words: frequent in it (`mintf`), present in other documents (`mindf`) and not in
  most of the index (`maxdf`, half the documents), so function words drop out
  without a stop list.
- **vector**: chunk vectors are not stored, so they cannot be read back. Up to
  four passages spread over the document's stored text are embedded with the
  search embedder and averaged into one vector; the nearest chunks of other
  documents vote for their document (best chunk first).

Every candidate is filtered to the collections the caller may read, and the
document itself needs reader on its collection.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from oss_core.vectors import UnsupportedDimensionError, vector_field
from oss_server import services
from oss_server.api import search as search_mod
from oss_server.api.access import check, read_scope
from oss_server.catalogue.models import Document

log = logging.getLogger(__name__)

MODES = ("hybrid", "text", "vector")
MAX_ROWS = 50
PASSAGES = 4
PASSAGE_CHARS = 1200
RRF_K = 60
MLT_FIELDS = "title_txt,text_txt"
MLT_MAX_TERMS = 30
MLT_MAX_TOKENS = 50000  # tokens of the source text MLT reads per field (Lucene default: 5000)
HIT_FIELDS = (
    "id,collection_s,uri_s,title_txt,media_type_s,content_type_group_s,language_s,modified_dt"
)


class SimilarHit(Schema):
    id: str
    title: str
    uri: str
    collection: str
    media_type: str | None = None
    content_type_group: str | None = None
    language: str | None = None
    modified_at: str | None = None
    score: float
    text_rank: int | None = None  # 1-based rank in the text (MoreLikeThis) list
    vector_rank: int | None = None  # 1-based rank in the vector list


class SimilarOut(Schema):
    document_id: str
    mode: str
    hits: list[SimilarHit]
    text_error: str | None = None
    vector_error: str | None = None


def mlt_params(doc_id: str, fq: list[str], k: int, index_size: int) -> dict[str, Any]:
    """The `{!mlt}` query for a document. `index_size` (documents in the whole index) sets
    `maxdf`: a word in more than half the index says nothing about likeness."""

    mindf = 2 if index_size > 2 else 1
    maxdf = max(mindf, index_size // 2)
    local = (
        f"{{!mlt qf={MLT_FIELDS} mintf=2 mindf={mindf} maxdf={maxdf} minwl=3 "
        f"maxqt={MLT_MAX_TERMS} maxntp={MLT_MAX_TOKENS}}}"
    )
    return {
        "q": local + doc_id,
        "defType": "lucene",
        "fq": [*fq, "-id:" + search_mod._escape(doc_id)],
        "rows": k,
        "fl": "id,score",
        "hl": False,
    }


def passages(text: str, n: int = PASSAGES, size: int = PASSAGE_CHARS) -> list[str]:
    """Up to `n` passages of about `size` characters spread evenly over the text."""

    text = text.strip()
    if not text:
        return []
    if len(text) <= size * n:
        return [text[i : i + size] for i in range(0, len(text), size)][:n]
    step = (len(text) - size) / (n - 1) if n > 1 else 0
    return [text[int(i * step) : int(i * step) + size] for i in range(n)]


def centroid(vectors: list[list[float]]) -> list[float]:
    """The mean of the vectors, scaled to unit length (cosine similarity ignores length)."""

    if not vectors:
        return []
    dim = len(vectors[0])
    mean = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in mean)) or 1.0
    return [x / norm for x in mean]


def fuse(*ranked: list[str]) -> dict[str, float]:
    """Reciprocal rank fusion of ranked id lists."""

    scores: dict[str, float] = {}
    for ids in ranked:
        for i, did in enumerate(ids):
            scores[did] = scores.get(did, 0.0) + 1.0 / (RRF_K + i)
    return scores


def _text_ranking(client: Any, doc_id: str, fq: list[str], k: int) -> list[str]:
    body = client.select(services.solr_documents_collection(), q="*:*", rows=0, hl=False)
    size = int(body.get("response", {}).get("numFound", 0))
    body = client.select(services.solr_documents_collection(), **mlt_params(doc_id, fq, k, size))
    return [str(d["id"]) for d in body.get("response", {}).get("docs", []) if d["id"] != doc_id]


def _vector_ranking(client: Any, doc: Document, fq: list[str], k: int) -> list[str]:
    texts = passages(doc.text)
    if not texts:
        raise ValueError("the document has no stored text to embed")
    embedder = search_mod.query_embedder()
    vector = centroid([list(v) for v in embedder.embed(texts)])
    try:
        field = vector_field(len(vector))
    except UnsupportedDimensionError as exc:
        raise ValueError(str(exc)) from exc
    chunk_fq = [*fq, "-document_id:" + search_mod._escape(doc.id)]
    model = str(getattr(embedder, "model_name", "") or "")
    if model:
        chunk_fq.append(f"model_s:{search_mod._escape(model)}")
    body = client.select(
        services.solr_chunks_collection(),
        q=f"{{!knn f={field} topK={k * 5}}}" + "[" + ",".join(f"{x:.6f}" for x in vector) + "]",
        fq=chunk_fq,
        rows=k * 5,
        fl="document_id,score",
        hl=False,
    )
    order: list[str] = []
    for ch in body.get("response", {}).get("docs", []):
        did = str(ch.get("document_id") or "")
        if did and did != doc.id and did not in order:
            order.append(did)
    return order[:k]


def similar_documents(
    request: HttpRequest,
    doc_id: str,
    mode: str = "hybrid",
    rows: int = 10,
    collection: str | None = None,
) -> SimilarOut:
    """Documents like this one: MoreLikeThis on the text, nearest neighbours on the vectors,
    or both fused (`mode=hybrid|text|vector`). `collection` limits the candidates to one
    collection; without it they come from every collection the caller may read."""

    doc = get_object_or_404(Document.objects.select_related("collection"), pk=doc_id)
    check(request, "reader", doc.collection.slug)
    if mode not in MODES:
        raise HttpError(422, f"mode must be one of {', '.join(MODES)}")
    rows = max(1, min(rows, MAX_ROWS))
    allowed = read_scope(request, collection)
    fq = search_mod.collection_filter(allowed)
    if collection:
        fq.append(f"collection_s:{search_mod._escape(collection)}")
    client = services.solr_client()
    k = max(rows * 3, 30)
    text_ids: list[str] = []
    vector_ids: list[str] = []
    text_error = vector_error = None
    if mode in ("hybrid", "text"):
        try:
            text_ids = _text_ranking(client, doc.id, fq, k)
        except Exception as exc:  # noqa: BLE001 - report it; the other signal may still answer
            log.warning("more-like-this text query failed for %s: %s", doc.id, exc)
            text_error = str(exc)[:300]
    if mode in ("hybrid", "vector"):
        try:
            vector_ids = _vector_ranking(client, doc, fq, k)
        except Exception as exc:  # noqa: BLE001 - no embedder or no chunks: say so
            log.info("more-like-this vector query unavailable for %s: %s", doc.id, exc)
            vector_error = str(exc)[:300]
    scores = fuse(text_ids, vector_ids)
    ordered = sorted(scores, key=lambda d: -scores[d])[:rows]
    hits: list[SimilarHit] = []
    if ordered:
        # the candidates' metadata, filtered by read scope once more
        body = client.select(
            services.solr_documents_collection(),
            q="{!terms f=id}" + ",".join(ordered),
            defType="lucene",
            fq=fq,
            rows=len(ordered),
            fl=HIT_FIELDS,
            hl=False,
        )
        found = {str(d["id"]): d for d in body.get("response", {}).get("docs", [])}
        for did in ordered:
            d = found.get(did)
            if d is None:
                continue
            title = d.get("title_txt")
            if isinstance(title, list):
                title = title[0] if title else ""
            hits.append(
                SimilarHit(
                    id=did,
                    title=str(title or d.get("uri_s") or ""),
                    uri=str(d.get("uri_s") or ""),
                    collection=str(d.get("collection_s") or ""),
                    media_type=d.get("media_type_s"),
                    content_type_group=d.get("content_type_group_s"),
                    language=d.get("language_s"),
                    modified_at=d.get("modified_dt"),
                    score=round(scores[did], 5),
                    text_rank=text_ids.index(did) + 1 if did in text_ids else None,
                    vector_rank=vector_ids.index(did) + 1 if did in vector_ids else None,
                )
            )
    return SimilarOut(
        document_id=doc.id,
        mode=mode,
        hits=hits,
        text_error=text_error,
        vector_error=vector_error,
    )


def mount(router: Router) -> None:
    router.get("/documents/{doc_id}/similar", response=SimilarOut, tags=["documents"])(
        similar_documents
    )
