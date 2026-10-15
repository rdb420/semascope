"""Search: turn an API request into a Solr query and back."""

from __future__ import annotations

import re
from typing import Any

from oss_pipeline.clients.solr import SolrClient
from oss_server.api.schemas import FacetValue, SearchHit, SearchIn, SearchOut

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


def _escape(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_params(req: SearchIn) -> dict[str, Any]:
    fq: list[str] = []
    if req.collection:
        fq.append(f"collection_s:{_escape(req.collection)}")
    for field, values in req.filters.items():
        if not values or not (
            field in ALLOWED_FACETS or field.endswith(("_s", "_ss", "_b", "_paths"))
        ):
            continue
        fq.append(
            "{!tag=" + field + "}" + field + ":(" + " OR ".join(_escape(v) for v in values) + ")"
        )
    facets = [f for f in req.facets if f in ALLOWED_FACETS or f.endswith(("_ss", "_paths"))]
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
    if req.histogram and (req.histogram.endswith("_dt") or req.histogram.endswith("_dts")):
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


def search(client: SolrClient, collection_name: str, req: SearchIn) -> SearchOut:
    extra = [f for f in scheme_facets() if f not in req.facets]
    if extra:
        req = req.model_copy(update={"facets": [*req.facets, *extra]})
    if req.mode in ("semantic", "hybrid") and req.q.strip() and req.q.strip() != "*:*":
        return _semantic_search(client, collection_name, req)
    body = client.select(collection_name, **build_params(req))
    return parse_response(req, body)


def _knn_chunks(client: SolrClient, req: SearchIn, k: int) -> list[dict[str, Any]]:
    """Top-k chunks by vector similarity, with their passage text."""

    from oss_core.config import get_settings
    from oss_pipeline.embedders import get_embedder
    from oss_server.services import solr_chunks_collection

    s = get_settings()
    embedder = get_embedder(s.semascope_embedder, s.semascope_embed_model)
    query_vec = getattr(embedder, "embed_query", None)
    vector = query_vec(req.q) if query_vec else embedder.embed([req.q])[0]
    fq = [f'collection_s:"{req.collection}"'] if req.collection else []
    body = client.select(
        solr_chunks_collection(),
        q="{!knn f=vector topK=" + str(k) + "}" + "[" + ",".join(f"{x:.6f}" for x in vector) + "]",
        fq=fq,
        rows=k,
        fl="id,document_id,text_txt,page_i,score",
        hl=False,
    )
    return list(body.get("response", {}).get("docs", []))


def _semantic_search(client: SolrClient, collection_name: str, req: SearchIn) -> SearchOut:
    """Semantic: rank documents by their best chunk. Hybrid: reciprocal rank fusion with keyword."""

    k = max(50, req.page_size * 10)
    chunks = _knn_chunks(client, req, k)
    doc_order: list[str] = []
    passages: dict[str, list[str]] = {}
    for ch in chunks:
        did = str(ch["document_id"])
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
        kw_body = client.select(collection_name, **build_params(kw))
        for i, d in enumerate(kw_body.get("response", {}).get("docs", [])):
            ranks[d["id"]] = ranks.get(d["id"], 0.0) + 1.0 / (60 + i)
    ordered = sorted(ranks, key=lambda d: -ranks[d])
    start = (max(req.page, 1) - 1) * req.page_size
    page_ids = ordered[start : start + req.page_size]
    if not page_ids:
        return SearchOut(
            total=len(ordered),
            page=req.page,
            page_size=req.page_size,
            took_ms=0,
            hits=[],
            facets={},
            histogram=[],
        )
    # fetch the page's documents, with facets over the whole fused set
    id_query = " OR ".join(f'id:"{d}"' for d in ordered[:1000])
    fetch = req.model_copy(
        update={
            "q": id_query,
            "page": 1,
            "page_size": len(ordered[:1000]),
            "highlight": req.highlight,
            "mode": "keyword",
        }
    )
    params = build_params(fetch)
    params["q"] = id_query
    params["defType"] = "lucene"
    params["df"] = "text_txt"
    params["hl.q"] = "{!edismax qf='title_txt text_txt' v=$hlq}"
    params["hlq"] = req.q
    body = client.select(collection_name, **params)
    parsed = parse_response(fetch, body)
    by_id = {h.id: h for h in parsed.hits}
    hits = []
    for did in page_ids:
        hit = by_id.get(did)
        if hit is None:
            continue
        hit.passages = passages.get(did, [])
        hit.score = round(ranks[did], 5)
        hits.append(hit)
    return SearchOut(
        total=len(ordered),
        page=req.page,
        page_size=req.page_size,
        took_ms=parsed.took_ms,
        hits=hits,
        facets=parsed.facets,
        histogram=parsed.histogram,
    )
