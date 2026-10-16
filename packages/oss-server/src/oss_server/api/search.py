"""Search: turn an API request into a Solr query and back."""

from __future__ import annotations

import logging
import re
from typing import Any

from oss_core.vectors import UnsupportedDimensionError, vector_field
from oss_pipeline.clients.solr import SolrClient, quote_term
from oss_server.api.schemas import FacetValue, SearchHit, SearchIn, SearchOut

log = logging.getLogger(__name__)

ALLOWED_FACETS = {
    "content_type_group_s",
    "language_s",
    "media_type_s",
    "source_id_s",
    "collection_s",
    "ocr_b",
    "emails_ss",
    "urls_ss",
    "phones_ss",
    "money_ss",
    "hashtags_ss",
    "persons_ss",
    "organisations_ss",
    "places_ss",
    "concept_labels_ss",
    "entity_labels_ss",
    "pack_versions_ss",
}
HIT_FIELDS = (
    "id,collection_s,source_id_s,uri_s,title_txt,media_type_s,content_type_group_s,language_s,"
    "size_l,modified_dt,indexed_dt,page_count_i,preview_pages_i,score"
)
ALLOWED_SORTS = {
    "score desc",
    "modified_dt desc",
    "modified_dt asc",
    "indexed_dt desc",
    "title_s asc",
    "size_l desc",
}


# A field name the request may name (a filter, a facet, the histogram): letters, digits,
# underscores and hyphens (scheme slugs), so it can never close local params or a clause
FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,127}")
FILTER_SUFFIXES = ("_s", "_ss", "_b", "_paths")
FACET_SUFFIXES = ("_ss", "_paths")
DATE_SUFFIXES = ("_dt", "_dts")


def _escape(value: str) -> str:
    return quote_term(value)


def field_allowed(name: str, suffixes: tuple[str, ...]) -> bool:
    """A known facet field, or a well-formed dynamic field with one of `suffixes`."""

    if not isinstance(name, str) or not FIELD_NAME.fullmatch(name):
        return False
    return name in ALLOWED_FACETS or name.endswith(suffixes)


def collection_filter(allowed: list[str] | None) -> list[str]:
    """The filter query that keeps a search inside the readable collections (none for all)."""

    if allowed is None:
        return []
    if not allowed:
        return ["-*:*"]
    return ["collection_s:(" + " OR ".join(_escape(c) for c in allowed) + ")"]


def facet_filters(req: SearchIn) -> list[tuple[str, str]]:
    """The request's facet filters as (field, clause) pairs, unknown fields dropped."""

    out: list[tuple[str, str]] = []
    for field, values in req.filters.items():
        if not values or not field_allowed(field, FILTER_SUFFIXES):
            continue
        out.append((field, field + ":(" + " OR ".join(_escape(v) for v in values) + ")"))
    return out


def build_params(req: SearchIn, allowed: list[str] | None = None) -> dict[str, Any]:
    fq: list[str] = collection_filter(allowed)
    if req.collection:
        fq.append(f"collection_s:{_escape(req.collection)}")
    for field, clause in facet_filters(req):
        fq.append("{!tag=" + field + "}" + clause)
    facets = [f for f in req.facets if field_allowed(f, FACET_SUFFIXES)]
    params: dict[str, Any] = {
        "q": req.q.strip() or "*:*",
        "fl": HIT_FIELDS,
        "fq": fq,
        "start": (max(req.page, 1) - 1) * req.page_size,
        "rows": min(max(req.page_size, 1), 100),
        "hl": req.highlight,
    }
    if req.sort and req.sort in ALLOWED_SORTS:
        params["sort"] = req.sort
    if facets:
        params["facet"] = True
        params["facet.mincount"] = 1
        params["facet.limit"] = min(req.facet_limit, 100)
        params["facet.field"] = ["{!ex=" + f + "}" + f for f in facets]
    if (
        req.histogram
        and FIELD_NAME.fullmatch(req.histogram)
        and req.histogram.endswith(DATE_SUFFIXES)
    ):
        valid = re.fullmatch(r"\+\d{1,3}(DAY|MONTH|YEAR)S?", req.histogram_gap)
        gap = req.histogram_gap if valid else "+1YEAR"
        params["facet"] = True
        params["facet.range"] = req.histogram
        params["facet.range.start"] = "NOW/YEAR-200YEARS" if "YEAR" in gap else "NOW/DAY-5YEARS"
        params["facet.range.end"] = "NOW/DAY+1DAY"
        params["facet.range.gap"] = gap
        params["facet.range.other"] = "all"
        params["facet.mincount"] = 1
    return params


def parse_response(req: SearchIn, body: dict[str, Any]) -> SearchOut:
    response = body.get("response", {})
    highlighting = body.get("highlighting", {})
    hits: list[SearchHit] = []
    for doc in response.get("docs", []):
        snippets = highlighting.get(doc["id"], {}).get("text_txt", []) if req.highlight else []
        hits.append(
            SearchHit(
                id=doc["id"],
                score=doc.get("score"),
                title=doc.get("title_txt") or doc.get("uri_s", ""),
                uri=doc.get("uri_s", ""),
                collection=doc.get("collection_s", ""),
                source_id=doc.get("source_id_s", ""),
                media_type=doc.get("media_type_s"),
                content_type_group=doc.get("content_type_group_s"),
                language=doc.get("language_s"),
                size=doc.get("size_l"),
                page_count=doc.get("page_count_i"),
                modified_at=doc.get("modified_dt"),
                indexed_at=doc.get("indexed_dt"),
                preview_pages=int(doc.get("preview_pages_i") or 0),
                snippets=snippets,
            )
        )
    facets: dict[str, list[FacetValue]] = {}
    for field, flat in body.get("facet_counts", {}).get("facet_fields", {}).items():
        facets[field] = [
            FacetValue(value=str(flat[i]), count=int(flat[i + 1])) for i in range(0, len(flat), 2)
        ]
    histogram: list[FacetValue] = []
    ranges = body.get("facet_counts", {}).get("facet_ranges", {})
    if req.histogram and req.histogram in ranges:
        flat = ranges[req.histogram].get("counts", [])
        histogram = [
            FacetValue(value=str(flat[i])[:10], count=int(flat[i + 1]))
            for i in range(0, len(flat), 2)
        ]

    return SearchOut(
        total=int(response.get("numFound", 0)),
        page=req.page,
        page_size=req.page_size,
        took_ms=int(body.get("responseHeader", {}).get("QTime", 0)),
        hits=hits,
        facets=facets,
        histogram=histogram,
    )


def scheme_facets() -> list[str]:
    """One `<scheme>_paths` facet per scheme of every active pack with taggable concepts."""

    from oss_server.knowledge.models import Concept, Pack

    slugs = (
        Concept.objects.filter(
            pack__status=Pack.Status.ACTIVE, kind=Concept.Kind.CONCEPT, taggable=True
        )
        .exclude(scheme_slug="")
        .values_list("scheme_slug", flat=True)
        .distinct()
    )
    return [f"{slug}_paths" for slug in sorted(set(slugs))]


def entity_class_facets() -> list[str]:
    """One `x_<class>_entities_ss` facet per class of the active packs (zero-shot providers)."""

    from oss_pipeline.stages.ner import facet_for
    from oss_server.pipeline.tasks import active_entity_labels

    return sorted(
        {facet_for(label, spec["class"]) for label, spec in active_entity_labels().items()}
    )


def search(
    client: SolrClient, collection_name: str, req: SearchIn, allowed: list[str] | None = None
) -> SearchOut:
    """Run a search. `allowed` limits it to those collections (None: no limit)."""

    extra = [f for f in [*scheme_facets(), *entity_class_facets()] if f not in req.facets]
    if extra:
        req = req.model_copy(update={"facets": [*req.facets, *extra]})
    if req.mode in ("semantic", "hybrid") and req.q.strip() and req.q.strip() != "*:*":
        return _semantic_search(client, collection_name, req, allowed)
    body = client.select(collection_name, **build_params(req, allowed))
    return parse_response(req, body)


def query_embedder() -> Any:
    """The embedder search shares with the embed stage (same provider, model and endpoint)."""

    from oss_core.config import get_settings
    from oss_pipeline.embedders import get_embedder

    s = get_settings()
    return get_embedder(
        s.semascope_embedder,
        s.semascope_embed_model,
        s.semascope_embed_base_url,
        s.remote_key("embed"),
    )


def current_embed_model() -> str:
    try:
        return str(getattr(query_embedder(), "model_name", "") or "")
    except Exception:  # noqa: BLE001 - endpoint unset or model missing
        from oss_core.config import get_settings

        return get_settings().semascope_embed_model


def _knn_chunks(
    client: SolrClient,
    req: SearchIn,
    k: int,
    allowed: list[str] | None = None,
    documents: str = "documents",
) -> list[dict[str, Any]]:
    """Top-k chunks by vector similarity, with their passage text.

    Only chunks embedded by the current model take part: vectors of different
    models are not comparable, so after a provider change the semantic modes
    see the re-embedded documents only until `reindex --stage embed` is done.

    Filters are pre-filters: Solr applies `fq` before picking the top k. Chunks
    carry their collection; facet filters live on the documents, so they reach
    the chunks through a join on the document id. Where the join is not
    available (a sharded documents collection), the candidates are filtered
    afterwards instead, which keeps results correct but may find fewer.
    """

    from oss_server.services import solr_chunks_collection

    embedder = query_embedder()
    query_vec = getattr(embedder, "embed_query", None)
    vector = query_vec(req.q) if query_vec else embedder.embed([req.q])[0]
    fq = collection_filter(allowed)
    if req.collection:
        fq.append(f"collection_s:{_escape(req.collection)}")
    model = str(getattr(embedder, "model_name", "") or "")
    if model:
        fq.append(f"model_s:{quote_term(model)}")
    try:
        field = vector_field(len(vector))  # the field of this model's dimension
    except UnsupportedDimensionError as exc:
        from ninja.errors import HttpError

        raise HttpError(422, str(exc)) from exc
    params: dict[str, Any] = {
        "q": f"{{!knn f={field} topK={k}}}" + "[" + ",".join(f"{x:.6f}" for x in vector) + "]",
        "fq": fq,
        "rows": k,
        "fl": "id,document_id,text_txt,page_i,score",
        "hl": False,
    }
    clauses = [clause for _, clause in facet_filters(req)]
    if clauses:
        joined = {
            **params,
            "fq": [*fq, "{!join fromIndex=" + documents + " from=id to=document_id v=$docfq}"],
            "docfq": " AND ".join(f"({c})" for c in clauses),
        }
        try:
            body = client.select(solr_chunks_collection(), **joined)
            return list(body.get("response", {}).get("docs", []))
        except Exception:  # noqa: BLE001 - no join here: filter the candidates afterwards
            log.info("chunks: join pre-filter unavailable; filtering the candidates afterwards")
    body = client.select(solr_chunks_collection(), **params)
    return list(body.get("response", {}).get("docs", []))


def _semantic_search(
    client: SolrClient, collection_name: str, req: SearchIn, allowed: list[str] | None = None
) -> SearchOut:
    """Semantic: rank documents by their best chunk. Hybrid: reciprocal rank fusion with keyword."""

    k = max(200, req.page_size * 20)
    chunks = _knn_chunks(client, req, k, allowed, collection_name)
    # rank documents by their best chunk, but count at most three chunks per document so one
    # long text cannot crowd out every other hit
    doc_order: list[str] = []
    passages: dict[str, list[str]] = {}
    seen_per_doc: dict[str, int] = {}
    for ch in chunks:
        did = str(ch["document_id"])
        seen_per_doc[did] = seen_per_doc.get(did, 0) + 1
        if seen_per_doc[did] > 3:
            continue
        if did not in passages:
            doc_order.append(did)
            passages[did] = []
        if len(passages[did]) < 2:
            passages[did].append(str(ch.get("text_txt") or "")[:400])
    ranks: dict[str, float] = {did: 1.0 / (60 + i) for i, did in enumerate(doc_order)}
    if req.mode == "hybrid":
        kw = req.model_copy(
            update={"page": 1, "page_size": k, "facets": [], "highlight": False, "mode": "keyword"}
        )
        kw_body = client.select(collection_name, **build_params(kw, allowed))
        for i, d in enumerate(kw_body.get("response", {}).get("docs", [])):
            ranks[d["id"]] = ranks.get(d["id"], 0.0) + 1.0 / (60 + i)
    ordered = sorted(ranks, key=lambda d: -ranks[d])[:1000]
    if not ordered:
        return SearchOut(
            total=0,
            page=req.page,
            page_size=req.page_size,
            took_ms=0,
            hits=[],
            facets={},
            histogram=[],
        )
    # 1. which candidates pass every filter (facets, collection, read scope), with facets
    #    and the histogram over that filtered set
    candidates = req.model_copy(update={"highlight": False, "mode": "keyword"})
    params = build_params(candidates, allowed)
    params.update(
        q="{!terms f=id}" + ",".join(ordered), defType="lucene", start=0, rows=len(ordered)
    )
    params["fl"] = "id"
    body = client.select(collection_name, **params)
    passing = {str(d["id"]) for d in body.get("response", {}).get("docs", [])}
    counted = parse_response(candidates, body)
    filtered = [d for d in ordered if d in passing]
    start = (max(req.page, 1) - 1) * req.page_size
    page_ids = filtered[start : start + req.page_size]
    hits: list[SearchHit] = []
    took = counted.took_ms
    if page_ids:
        # 2. the page's documents, with highlighting
        page = req.model_copy(
            update={"page": 1, "facets": [], "histogram": None, "mode": "keyword", "filters": {}}
        )
        params = build_params(page, allowed)
        params.update(
            q="{!terms f=id}" + ",".join(page_ids),
            defType="lucene",
            df="text_txt",
            start=0,
            rows=len(page_ids),
        )
        params["hl.q"] = "{!edismax qf='title_txt text_txt' v=$hlq}"
        params["hlq"] = req.q
        page_body = client.select(collection_name, **params)
        took += int(page_body.get("responseHeader", {}).get("QTime", 0))
        by_id = {h.id: h for h in parse_response(page, page_body).hits}
        for did in page_ids:
            hit = by_id.get(did)
            if hit is None:
                continue
            hit.passages = passages.get(did, [])
            hit.score = round(ranks[did], 5)
            hits.append(hit)
    return SearchOut(
        total=len(filtered),
        page=req.page,
        page_size=req.page_size,
        took_ms=took,
        hits=hits,
        facets=counted.facets,
        histogram=counted.histogram,
    )
