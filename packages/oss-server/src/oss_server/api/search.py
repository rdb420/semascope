"""Search: turn an API request into a Solr query and back."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from ninja.errors import HttpError

from oss_core.vectors import UnsupportedDimensionError, vector_field
from oss_pipeline.clients.solr import SolrClient, quote_term
from oss_pipeline.languages import LANGUAGES, TEXT_PREFIX, TITLE_PREFIX
from oss_server.api.schemas import MAX_PAGE_SIZE, FacetValue, SearchHit, SearchIn, SearchOut

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
# A hit shows a few of these per document (SearchHit.entities)
HIT_ENTITY_FIELDS = (
    "persons_ss",
    "places_ss",
    "organisations_ss",
    "entity_labels_ss",
    "concept_labels_ss",
)
HIT_ENTITIES_PER_FIELD = 5
HIT_FIELDS = (
    "id,collection_s,source_id_s,uri_s,title_txt,media_type_s,content_type_group_s,language_s,"
    "size_l,modified_dt,indexed_dt,page_count_i,preview_pages_i,score,"
    + ",".join(HIT_ENTITY_FIELDS)
)
# "newest first" for a search without words (SRCH-07); a search with words ranks by relevance
NEWEST_FIRST = "modified_dt desc,indexed_dt desc"
ALLOWED_SORTS = {
    "score desc",
    NEWEST_FIRST,
    "modified_dt desc",
    "modified_dt asc",
    "indexed_dt desc",
    "indexed_dt asc",
    "title_s asc",
    "title_s desc",
    "size_l desc",
    "size_l asc",
    "page_count_i desc",
}
# Solr's own limit on how deep a page may start; deeper sets are exported (docs/search.md)
MAX_START = 10000
# semantic and hybrid modes rank at most this many documents (docs/search.md)
MAX_FUSED = 1000


# A field name the request may name (a filter, a facet, the histogram): letters, digits,
# underscores and hyphens (scheme slugs), so it can never close local params or a clause
FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,127}")
FILTER_SUFFIXES = ("_s", "_ss", "_b", "_paths")
FACET_SUFFIXES = ("_ss", "_paths")
DATE_SUFFIXES = ("_dt", "_dts")

# --- query fields (docs/search.md) ------------------------------------------------------------
# The neutral fields hold every document's words as written (lowercased, accents folded);
# the language fields hold the stemmed words of the documents in that language. Stemmed
# matches weigh less than exact ones, so a text with the very words ranks first.
NEUTRAL_TITLE = "title_txt^4"
NEUTRAL_TEXT = "text_txt^1"
STEMMED_TITLE = " ".join(f"{TITLE_PREFIX}{c}^2" for c in sorted(LANGUAGES))
STEMMED_TEXT = " ".join(f"{TEXT_PREFIX}{c}^0.5" for c in sorted(LANGUAGES))


def query_fields(stemming: bool = True) -> dict[str, str]:
    """`qf` and the field aliases users may type (`exact:`, `title:`, `text:`)."""

    title = NEUTRAL_TITLE + (" " + STEMMED_TITLE if stemming else "")
    text = NEUTRAL_TEXT + (" " + STEMMED_TEXT if stemming else "")
    return {
        "qf": f"{title} {text} uri_s^0.2",
        "f.exact.qf": f"{NEUTRAL_TITLE} {NEUTRAL_TEXT}",
        "f.title.qf": title,
        "f.text.qf": text,
    }


# --- operators --------------------------------------------------------------------------------
# A query's words are required as `mm` in solrconfig.xml says (all of one or two words, all
# but one of three to five, 80% of more). An explicit OR joins its neighbours instead: the
# words or groups on either side of it become one alternative, so `odysseus ithaca OR penelope`
# needs Odysseus and either Ithaca or Penelope. Solr's own reading would count each side of
# the OR as a required word, so `odysseus OR arjuna` found only texts naming both (SEMA-028
# follow-up: "OR behaves as AND").
OPERATORS = {"all": "100%", "any": "1"}


def _units(q: str) -> list[str] | None:
    """The top-level parts of a query: words, phrases and parenthesised groups, each with its
    field and +/- prefix. None when quotes or parentheses do not balance."""

    units: list[str] = []
    i = 0
    while i < len(q):
        if q[i].isspace():
            i += 1
            continue
        start = i
        # prefix and field
        m = re.match(r"[+-]?(?:[A-Za-z_][\w.-]*:)?", q[i:])
        i += m.end() if m else 0
        if i < len(q) and q[i] == '"':
            j = i + 1
            while j < len(q) and q[j] != '"':
                j += 2 if q[j] == "\\" else 1
            if j >= len(q):
                return None
            i = j + 1
            tail = re.match(r"~\d+", q[i:])
            i += tail.end() if tail else 0
        elif i < len(q) and q[i] == "(":
            depth, j, quoted = 0, i, False
            while j < len(q):
                ch = q[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == '"':
                    quoted = not quoted
                elif not quoted and ch == "(":
                    depth += 1
                elif not quoted and ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if depth != 0 or j >= len(q):
                return None
            i = j + 1
            tail = re.match(r"[~^][\d.]+", q[i:])
            i += tail.end() if tail else 0
        else:
            while i < len(q) and not q[i].isspace() and q[i] not in '"()':
                i += 2 if q[i] == "\\" else 1
            if i < len(q) and q[i] in '"(' and i == start:
                return None
        if i < len(q) and q[i] == ")":
            return None
        units.append(q[start:i])
    return units


def prepare_query(q: str, operator: str | None = None) -> tuple[str, str | None]:
    """The query text Solr gets and the `mm` to send with it (None: solrconfig's default)."""

    q = q.strip()
    if operator == "phrase":
        if q.startswith('"') and q.endswith('"') and len(q) > 1:
            return q, None
        return quote_term(q), None
    if operator in OPERATORS:
        return q, OPERATORS[operator]
    units = _units(q)
    if not units or "OR" not in units:
        return q, None
    # chains of units joined by OR become one group each
    parts: list[list[str]] = []
    joined = False
    for unit in units:
        if unit == "OR":
            joined = bool(parts)
            continue
        if joined and parts:
            parts[-1].append(unit)
        else:
            parts.append([unit])
        joined = False
    rest = [p for p in parts if p not in (["AND"], ["NOT"])]
    if len(rest) == 1:
        # the whole query is one choice: Solr would unwrap a lone group, so relax mm instead
        return q, "1"
    return " ".join(p[0] if len(p) == 1 else "(" + " OR ".join(p) + ")" for p in parts), None


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
    """The request's facet filters as (field, clause) pairs, unknown fields dropped: any of a
    field's `filters` values, and none of its `exclude` values."""

    out: list[tuple[str, str]] = []
    for field, values in req.filters.items():
        if not values or not field_allowed(field, FILTER_SUFFIXES):
            continue
        out.append((field, field + ":(" + " OR ".join(_escape(v) for v in values) + ")"))
    for field, values in req.exclude.items():
        if not values or not field_allowed(field, FILTER_SUFFIXES):
            continue
        terms = " OR ".join(_escape(v) for v in values)
        out.append((field, f"(*:* -{field}:({terms}))"))
    return out


def date_filter(req: SearchIn) -> str | None:
    """The `dates` range as a filter clause (start inclusive, end exclusive), or None."""

    d = req.dates
    if d is None or not (d.start or d.end):
        return None
    if not FIELD_NAME.fullmatch(d.field) or not d.field.endswith(DATE_SUFFIXES):
        return None
    return f"{d.field}:[{d.start or '*'} TO {d.end or '*'}}}"


def match_all(q: str) -> bool:
    return q.strip() in ("", "*:*", "*")


def build_params(req: SearchIn, allowed: list[str] | None = None) -> dict[str, Any]:
    fq: list[str] = collection_filter(allowed)
    if req.collection:
        fq.append(f"collection_s:{_escape(req.collection)}")
    for field, clause in facet_filters(req):
        fq.append("{!tag=" + field + "}" + clause)
    dates = date_filter(req)
    if dates:
        fq.append(dates)
    facets = [f for f in req.facets if field_allowed(f, FACET_SUFFIXES)]
    rows = min(max(req.page_size, 0), MAX_PAGE_SIZE)
    start = (max(req.page, 1) - 1) * rows
    if start > MAX_START:
        raise HttpError(422, f"results deeper than {MAX_START} are not paged; export them instead")
    q, mm = ("*:*", None) if match_all(req.q) else prepare_query(req.q, req.operator)
    params: dict[str, Any] = {
        "q": q,
        "fl": HIT_FIELDS,
        "fq": fq,
        "start": start,
        "rows": rows,
        "hl": req.highlight and rows > 0,
        **query_fields(req.stemming),
    }
    if mm is not None:
        params["mm"] = mm
    if req.sort and req.sort in ALLOWED_SORTS:
        params["sort"] = req.sort
    elif match_all(req.q):
        params["sort"] = NEWEST_FIRST
    if facets:
        params["facet"] = True
        params["facet.mincount"] = 1
        params["facet.limit"] = min(max(req.facet_limit, 1), 100)
        params["facet.field"] = ["{!ex=" + f + "}" + f for f in facets]
        for f in facets:
            if f in req.facet_more:  # "show more": this facet's values up to the cap
                params[f"f.{f}.facet.limit"] = 100
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
        d = req.dates
        if dates and d is not None and d.field == req.histogram:
            # a drill-down: the bars of the chosen range only
            if d.start:
                params["facet.range.start"] = d.start
            if d.end:
                params["facet.range.end"] = d.end
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
                entities={
                    f: [str(v) for v in doc[f][:HIT_ENTITIES_PER_FIELD]]
                    for f in HIT_ENTITY_FIELDS
                    if isinstance(doc.get(f), list) and doc[f]
                },
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


# Pack facets change only when a pack is activated or retired: keep them per active-pack set
# rather than querying the vocabulary on every search (SEMA-075)
_pack_facets: dict[tuple[Any, ...], list[str]] = {}
# Folder-path facets: every `*_paths` field the index holds that is not a pack scheme, so a
# connector's path field shows as a tree whatever it is called (FACET-06). Looked up in Solr
# at most once a minute.
PATH_FACET_SECONDS = 60.0
_path_facets: dict[str, tuple[float, list[str]]] = {}


def pack_facets() -> list[str]:
    from oss_server.knowledge.models import Pack

    key = tuple(
        Pack.objects.filter(status=Pack.Status.ACTIVE)
        .order_by("pk")
        .values_list("pk", "activated_at", "manifest_hash")
    )
    if key not in _pack_facets:
        _pack_facets.clear()
        _pack_facets[key] = [*scheme_facets(), *entity_class_facets()]
    return list(_pack_facets[key])


def path_facets(client: SolrClient, collection_name: str) -> list[str]:
    """The index's `*_paths` fields that are not pack schemes (folder trees), newest first."""

    from oss_server.knowledge.models import Concept

    cached = _path_facets.get(collection_name)
    if cached and time.monotonic() - cached[0] < PATH_FACET_SECONDS:
        return list(cached[1])
    found: list[str] = []
    get = getattr(client, "_get", None)  # Solr's Luke handler lists the fields in use
    if get is not None:
        try:
            body = get(f"/{collection_name}/admin/luke", show="fields", numTerms=0)
            names = [str(n) for n in (body.get("fields") or {})]
            schemes = {
                f"{slug}_paths"
                for slug in Concept.objects.exclude(scheme_slug="")
                .values_list("scheme_slug", flat=True)
                .distinct()
            }
            found = sorted(
                n
                for n in names
                if n.endswith("_paths") and n not in schemes and FIELD_NAME.fullmatch(n)
            )
        except Exception:  # noqa: BLE001 - facets are optional; the search still runs
            log.info("search: could not list the index's path fields", exc_info=True)
    _path_facets[collection_name] = (time.monotonic(), found)
    return found


def clear_facet_caches() -> None:
    """Forget the cached pack and path facets (after a pack activation)."""

    _pack_facets.clear()
    _path_facets.clear()


def search(
    client: SolrClient, collection_name: str, req: SearchIn, allowed: list[str] | None = None
) -> SearchOut:
    """Run a search. `allowed` limits it to those collections (None: no limit)."""

    if req.facets:  # a caller that asks for no facets gets none
        wanted = [*pack_facets(), *path_facets(client, collection_name)]
        extra = [f for f in wanted if f not in req.facets]
        if extra:
            req = req.model_copy(update={"facets": [*req.facets, *extra]})
    if req.mode in ("semantic", "hybrid") and not match_all(req.q):
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
        raise HttpError(422, str(exc)) from exc
    params: dict[str, Any] = {
        "q": f"{{!knn f={field} topK={k}}}" + "[" + ",".join(f"{x:.6f}" for x in vector) + "]",
        "fq": fq,
        "rows": k,
        "fl": "id,document_id,text_txt,page_i,score",
        "hl": False,
    }
    clauses = [clause for _, clause in facet_filters(req)]
    dates = date_filter(req)
    if dates:
        clauses.append(dates)
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

    # candidates: enough for twenty pages, at least 200 and at most MAX_FUSED (NEW-11)
    k = min(max(200, req.page_size * 20), MAX_FUSED)
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
        kw_params = build_params(kw, allowed)
        kw_params.update(start=0, rows=k, fl="id")  # the whole keyword list, not one page
        kw_body = client.select(collection_name, **kw_params)
        for i, d in enumerate(kw_body.get("response", {}).get("docs", [])):
            ranks[d["id"]] = ranks.get(d["id"], 0.0) + 1.0 / (60 + i)
    ordered = sorted(ranks, key=lambda d: -ranks[d])[:MAX_FUSED]
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
