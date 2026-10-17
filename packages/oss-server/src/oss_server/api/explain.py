"""Why a hit ranked where it did (parity rows ANL-16, APP-07: the ranking-explain view).

`POST /api/explain` takes a search and one of its hits and answers, per signal:

- **keyword**: the hit's rank among the search's keyword results (the first 1,000),
  its score, the scoring clauses that matched (field, term, weight) and Solr's
  explanation tree. The whole tree carries corpus-wide term statistics (how many
  documents hold a word, across every collection), so only staff get it whole;
  other users get it without those nodes.
- **semantic** (semantic and hybrid modes): the hit's rank by its best passage among
  the nearest chunks, and its closest passages with their similarity.
- **fused** (hybrid): each signal's reciprocal-rank share and the sum, the score the
  hybrid list is ordered by.

The hit's document needs reader on its collection, and the search runs in the
collections the caller may read, as the search itself does.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from oss_core.vectors import vector_field
from oss_server import services
from oss_server.api import search as search_mod
from oss_server.api.access import check, read_scope
from oss_server.api.schemas import SearchIn
from oss_server.catalogue.models import Document

log = logging.getLogger(__name__)

RRF_K = 60  # as api/search.py fuses
MAX_RANK = search_mod.MAX_FUSED
MAX_CLAUSES = 50
MAX_PASSAGES = 3
# explanation nodes that report corpus statistics rather than this document's match
_STATISTICS = re.compile(r"^(idf|n, number of documents|N, total number|avgdl|docCount|docFreq)")
_WEIGHT = re.compile(r"^weight\((?P<clause>.+?) in \d+\)")


class ExplainIn(Schema):
    search: SearchIn
    doc_id: str


class Clause(Schema):
    clause: str
    weight: float


class KeywordExplain(Schema):
    rank: int | None  # 1-based among the first 1,000 keyword results; None: not among them
    score: float | None
    matched: bool
    clauses: list[Clause]
    # Solr's explanation: whole for staff, without corpus statistics for everyone else
    explanation: dict[str, Any] | None = None


class Passage(Schema):
    text: str
    page: int | None
    similarity: float
    chunk_rank: int | None  # 1-based among the nearest chunks of the search


class SemanticExplain(Schema):
    rank: int | None  # 1-based by best passage among the nearest chunks
    candidates: int  # how many chunks the search weighed
    passages: list[Passage]


class FusedExplain(Schema):
    keyword_share: float
    semantic_share: float
    score: float
    rank: int | None  # 1-based in the fused list, before facet filters


class ExplainOut(Schema):
    doc_id: str
    title: str
    collection: str
    mode: str
    query: str
    keyword: KeywordExplain | None
    semantic: SemanticExplain | None = None
    fused: FusedExplain | None = None
    semantic_error: str | None = None
    full: bool  # the explanation is whole (staff), not stripped of corpus statistics


def clauses(node: dict[str, Any] | None) -> list[Clause]:
    """The weighted query clauses of a Solr explanation (a `weight(field:term in n)` node
    each), heaviest first."""

    out: list[Clause] = []

    def walk(n: dict[str, Any]) -> None:
        m = _WEIGHT.match(str(n.get("description", "")))
        if m:
            out.append(Clause(clause=m.group("clause"), weight=round(float(n.get("value", 0)), 5)))
            return
        for child in n.get("details") or []:
            if isinstance(child, dict):
                walk(child)

    if node:
        walk(node)
    best: dict[str, Clause] = {}
    for c in out:  # a clause repeated under several fields' maxima: keep its best weight
        if c.clause not in best or best[c.clause].weight < c.weight:
            best[c.clause] = c
    return sorted(best.values(), key=lambda c: (-c.weight, c.clause))[:MAX_CLAUSES]


def without_statistics(node: dict[str, Any]) -> dict[str, Any]:
    """The explanation tree without the nodes that report corpus statistics."""

    kids = [
        without_statistics(c)
        for c in node.get("details") or []
        if isinstance(c, dict) and not _STATISTICS.match(str(c.get("description", "")))
    ]
    out = {k: v for k, v in node.items() if k != "details"}
    if kids:
        out["details"] = kids
    return out


def _keyword(
    client: Any, req: SearchIn, allowed: list[str] | None, doc_id: str, full: bool
) -> KeywordExplain:
    kw = req.model_copy(
        update={"mode": "keyword", "page": 1, "page_size": 1, "facets": [], "highlight": False}
    )
    params = search_mod.build_params(kw, allowed)
    ranking = {**params, "start": 0, "rows": MAX_RANK, "fl": "id", "hl": False}
    body = client.select(services.solr_documents_collection(), **ranking)
    order = [str(d["id"]) for d in body.get("response", {}).get("docs", [])]
    rank = order.index(doc_id) + 1 if doc_id in order else None
    probe = {
        **params,
        "fq": [*params.get("fq", []), "id:" + search_mod._escape(doc_id)],
        "start": 0,
        "rows": 1,
        "fl": "id,score",
        "hl": False,
        "debugQuery": "true",
        "debug.explain.structured": "true",
    }
    body = client.select(services.solr_documents_collection(), **probe)
    docs = body.get("response", {}).get("docs", [])
    explain = (body.get("debug", {}).get("explain") or {}).get(doc_id)
    tree = explain if isinstance(explain, dict) else None
    return KeywordExplain(
        rank=rank,
        score=float(docs[0]["score"]) if docs and docs[0].get("score") is not None else None,
        matched=bool(docs),
        clauses=clauses(tree),
        explanation=None if tree is None else tree if full else without_statistics(tree),
    )


def _nearest_passages(
    client: Any, req: SearchIn, doc_id: str, chunk_order: dict[str, int]
) -> list[Passage]:
    """The document's chunks nearest to the query (Solr filters before picking the top k,
    so this finds them even when the document is far down the search's list)."""

    from oss_server.services import solr_chunks_collection

    embedder = search_mod.query_embedder()
    embed_query = getattr(embedder, "embed_query", None)
    vector = embed_query(req.q) if embed_query else embedder.embed([req.q])[0]
    field = vector_field(len(vector))
    fq = ["document_id:" + search_mod._escape(doc_id)]
    model = str(getattr(embedder, "model_name", "") or "")
    if model:
        fq.append("model_s:" + search_mod._escape(model))
    body = client.select(
        solr_chunks_collection(),
        q=f"{{!knn f={field} topK={MAX_PASSAGES}}}"
        + "["
        + ",".join(f"{x:.6f}" for x in vector)
        + "]",
        fq=fq,
        rows=MAX_PASSAGES,
        fl="id,text_txt,page_i,score",
        hl=False,
    )
    return [
        Passage(
            text=str(ch.get("text_txt") or "")[:600],
            page=ch.get("page_i"),
            similarity=round(float(ch.get("score") or 0.0), 5),
            chunk_rank=chunk_order.get(str(ch.get("id"))),
        )
        for ch in body.get("response", {}).get("docs", [])
    ]


def _semantic(
    client: Any, req: SearchIn, allowed: list[str] | None, doc_id: str
) -> tuple[SemanticExplain, list[str]]:
    k = min(max(200, req.page_size * 20), search_mod.MAX_FUSED)
    chunks = search_mod._knn_chunks(client, req, k, allowed, services.solr_documents_collection())
    order: list[str] = []
    for ch in chunks:  # documents by their best chunk, as the search ranks them
        did = str(ch.get("document_id"))
        if did not in order:
            order.append(did)
    chunk_order = {str(ch.get("id")): i + 1 for i, ch in enumerate(chunks)}
    return (
        SemanticExplain(
            rank=order.index(doc_id) + 1 if doc_id in order else None,
            candidates=len(chunks),
            passages=_nearest_passages(client, req, doc_id, chunk_order),
        ),
        order,
    )


def _fused(
    client: Any, req: SearchIn, allowed: list[str] | None, doc_id: str, semantic_order: list[str]
) -> FusedExplain:
    k = min(max(200, req.page_size * 20), search_mod.MAX_FUSED)
    kw = req.model_copy(
        update={"page": 1, "page_size": k, "facets": [], "highlight": False, "mode": "keyword"}
    )
    params = search_mod.build_params(kw, allowed)
    params.update(start=0, rows=k, fl="id")
    body = client.select(services.solr_documents_collection(), **params)
    keyword_order = [str(d["id"]) for d in body.get("response", {}).get("docs", [])]
    scores: dict[str, float] = {}
    for ranked in (semantic_order, keyword_order):
        for i, did in enumerate(ranked):
            scores[did] = scores.get(did, 0.0) + 1.0 / (RRF_K + i)
    fused = sorted(scores, key=lambda d: -scores[d])
    kw_share = 1.0 / (RRF_K + keyword_order.index(doc_id)) if doc_id in keyword_order else 0.0
    sem_share = 1.0 / (RRF_K + semantic_order.index(doc_id)) if doc_id in semantic_order else 0.0
    return FusedExplain(
        keyword_share=round(kw_share, 6),
        semantic_share=round(sem_share, 6),
        score=round(kw_share + sem_share, 6),
        rank=fused.index(doc_id) + 1 if doc_id in scores else None,
    )


def explain_hit(request: HttpRequest, payload: ExplainIn) -> ExplainOut:
    """Why `doc_id` ranks where it does in `search`: its keyword rank, score and matching
    clauses; in semantic and hybrid modes its nearest passages and rank by meaning; in hybrid
    mode the reciprocal-rank shares the fused order adds up."""

    doc = get_object_or_404(
        Document.objects.select_related("collection").only(
            "id", "title", "uri", "collection__slug"
        ),
        pk=payload.doc_id,
    )
    check(request, "reader", doc.collection.slug)
    req = payload.search
    if search_mod.match_all(req.q):
        raise HttpError(422, "a search for everything ranks by date, not by relevance")
    allowed = read_scope(request, req.collection)
    user = getattr(request, "user", None)
    full = bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
    client = services.solr_client()
    out = ExplainOut(
        doc_id=doc.id,
        title=str(doc.title or doc.uri),
        collection=doc.collection.slug,
        mode=req.mode,
        query=req.q,
        keyword=None,
        full=full,
    )
    out.keyword = _keyword(client, req, allowed, doc.id, full)
    if req.mode in ("semantic", "hybrid"):
        try:
            semantic, order = _semantic(client, req, allowed, doc.id)
            out.semantic = semantic
            if req.mode == "hybrid":
                out.fused = _fused(client, req, allowed, doc.id, order)
        except Exception as exc:  # noqa: BLE001 - no embedder or no chunks: say so
            log.info("explain: semantic signal unavailable: %s", exc)
            out.semantic_error = str(exc)[:300]
    return out


def mount(router: Router) -> None:
    router.post("/explain", response=ExplainOut, tags=["search"])(explain_hit)
