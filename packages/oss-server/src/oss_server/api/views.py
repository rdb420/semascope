"""Views of documents and searches: originals, query matches, table, entities, graph, feeds.

One router, mounted at the API root, so its routes sit beside the ones they
extend: `/documents/{id}/original|access|matches`, `/views/...` for the
table, entities, alerts and co-occurrence graph views of a search, and
`/feeds/...` for Atom and RSS. Every read is checked per collection
(`access.check` for one document, `access.read_scope` for a search).
"""

from __future__ import annotations

import re
from bisect import bisect_right
from functools import cache
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router, Schema, Status
from ninja.errors import HttpError
from pydantic import ValidationError

from oss_server import services
from oss_server.api import feeds, files, graph
from oss_server.api import search as search_mod
from oss_server.api.access import ROLES, check, read_scope, role_for
from oss_server.api.auth import AUTH
from oss_server.api.errors import logged_detail
from oss_server.api.schemas import FacetValue, SearchHit, SearchIn
from oss_server.catalogue.models import Document, SavedSearch

router = Router(tags=["views"])
PAGE_SEPARATOR = "\n\n"  # what the structure stage joins pages with


def _user(request: HttpRequest) -> Any:
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        raise HttpError(401, "sign in")
    return user


def _readable_document(request: HttpRequest, doc_id: str) -> Document:
    d = get_object_or_404(Document.objects.select_related("collection"), pk=doc_id)
    check(request, "reader", d.collection.slug)
    return d


# --- the original file --------------------------------------------------------------------------


@router.get("/documents/{doc_id}/original", tags=["documents"])
def original(request: HttpRequest, doc_id: str, download: bool = False) -> HttpResponse:
    """The stored original. PDFs, images, plain text, audio and video open inline unless
    `download=true`; every other type (HTML and SVG included) is an attachment. Honours a
    single `Range: bytes=` request."""

    return files.serve_original(request, _readable_document(request, doc_id), download=download)


class AccessOut(Schema):
    collection: str
    role: str
    can_curate: bool
    can_edit: bool


@router.get("/documents/{doc_id}/access", response=AccessOut, tags=["documents"])
def document_access(request: HttpRequest, doc_id: str) -> AccessOut:
    """The caller's role on the document's collection, so the UI shows only what they may do."""

    d = _readable_document(request, doc_id)
    role = role_for(getattr(request, "user", None), d.collection.slug)
    rank = ROLES.index(role) if role in ROLES else -1
    return AccessOut(
        collection=d.collection.slug,
        role=role,
        can_curate=rank >= ROLES.index("curator"),
        can_edit=rank >= ROLES.index("editor"),
    )


# --- where a query matches in a document --------------------------------------------------------

_CLAUSE = re.compile(
    r"""(?P<neg>-|!|\bNOT\s+)?
        (?:(?P<field>[A-Za-z_][\w.]*):)?
        (?:"(?P<phrase>[^"]*)"(?:~\d+)?
          |(?P<range>[\[{][^\]}]*[\]}])
          |(?P<word>[^\s()"]+))""",
    re.X,
)
_OPERATORS = {"AND", "OR", "NOT", "&&", "||", "TO"}
_TEXT_FIELDS = ("title_txt", "text_txt")
_STRIP = re.compile(r"""[\\+\-!(){}\[\]^"~:/]""")


@cache
def _stopwords() -> frozenset[str]:
    from oss_server.api.searchplus import _stopwords as deployed

    return frozenset(deployed())


def query_terms(q: str) -> list[str]:
    """The words and phrases of a keyword query worth highlighting.

    Field clauses count only on text fields; negated clauses, ranges, boosts,
    local parameters and `*:*` are left out. A trailing `*` keeps its meaning (a
    prefix). Single stopwords (`and`, `the`) are left out; phrases keep theirs.
    Returns lower-cased terms, phrases with single spaces, in query order.
    """

    q = (q or "").strip()
    if not q or q == "*:*" or q.startswith("{!"):
        return []
    out: list[str] = []
    for m in _CLAUSE.finditer(q):
        if m.group("neg") or m.group("range"):
            continue
        field = m.group("field")
        if field and not (field in _TEXT_FIELDS or field.endswith("_txt")):
            continue
        if m.group("phrase") is not None:
            words = [_STRIP.sub("", w) for w in m.group("phrase").split()]
            term = " ".join(w for w in words if w)
        else:
            word = m.group("word")
            if word in _OPERATORS:
                continue
            word = re.sub(r"[~^][\d.]*$", "", word)
            term = _STRIP.sub("", word)
        term = term.lower()
        if len(term.replace("*", "").replace("?", "")) < 2 or term in out:
            continue
        if " " not in term and term in _stopwords():
            continue
        out.append(term)
    return out


def _word_pattern(word: str) -> str:
    if word.endswith("*") and "*" not in word[:-1]:
        stem = word[:-1]
        return re.escape(stem).replace(r"\?", r"\w").replace("?", r"\w") + r"\w*"
    if "*" in word or "?" in word:
        return "".join(r"\w*" if c == "*" else r"\w" if c == "?" else re.escape(c) for c in word)
    if len(word) >= 4:  # simple inflections: flood, floods, flooded, flooding
        return re.escape(word) + r"\w{0,3}"
    return re.escape(word)


def term_pattern(terms: list[str]) -> re.Pattern[str] | None:
    """One case-insensitive pattern for every term, as whole words."""

    alts = [r"\W+".join(_word_pattern(w) for w in t.split()) for t in terms if t]
    if not alts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)", re.I)


def page_starts(doc: Document) -> list[int]:
    stored = doc.fields.get("page_starts")
    if isinstance(stored, list) and stored and len(stored) == len(doc.pages):
        return [int(x) for x in stored]
    if not doc.pages or PAGE_SEPARATOR.join(doc.pages) != (doc.text or ""):
        return []
    starts, pos = [], 0
    for page in doc.pages:
        starts.append(pos)
        pos += len(page) + len(PAGE_SEPARATOR)
    return starts


class MatchSpan(Schema):
    start: int
    end: int
    page: int | None
    matched: str


class PageCount(Schema):
    page: int
    count: int


class MatchesOut(Schema):
    terms: list[str]
    total: int
    truncated: bool
    first_page: int | None
    pages: list[PageCount]
    spans: list[MatchSpan]


@router.get("/documents/{doc_id}/matches", response=MatchesOut, tags=["documents"])
def document_matches(
    request: HttpRequest, doc_id: str, q: str = "", limit: int = 500
) -> MatchesOut:
    """Where the words of a keyword query occur in the document's text, with their pages.

    Offsets are into the stored text, like tag spans; `first_page` is where the
    viewer jumps to. Matching is literal (case-insensitive, whole words, simple
    inflections, `*` as a prefix), so it may miss what stemming or synonyms found.
    """

    d = _readable_document(request, doc_id)
    terms = query_terms(q)
    pattern = term_pattern(terms)
    if pattern is None:
        return MatchesOut(
            terms=terms, total=0, truncated=False, first_page=None, pages=[], spans=[]
        )
    starts = page_starts(d)
    cap = max(1, min(limit, 5000))
    spans: list[MatchSpan] = []
    per_page: dict[int, int] = {}
    total = 0
    for m in pattern.finditer(d.text or ""):
        total += 1
        page = bisect_right(starts, m.start()) if starts else None
        if page is not None:
            per_page[page] = per_page.get(page, 0) + 1
        if len(spans) < cap:
            spans.append(MatchSpan(start=m.start(), end=m.end(), page=page, matched=m.group(0)))
    return MatchesOut(
        terms=terms,
        total=total,
        truncated=total > len(spans),
        first_page=min(per_page) if per_page else None,
        pages=[PageCount(page=p, count=n) for p, n in sorted(per_page.items())],
        spans=spans,
    )


# --- table view: column statistics of a search -------------------------------------------------


class NumberStats(Schema):
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    sum: float | None = None
    count: int = 0


class DateStats(Schema):
    min: str | None = None
    max: str | None = None
    count: int = 0


class TableStatsOut(Schema):
    total: int
    size: NumberStats
    pages: NumberStats
    modified: DateStats
    indexed: DateStats
    types: list[FacetValue]
    languages: list[FacetValue]


def _keyword_params(req: SearchIn, allowed: list[str] | None) -> dict[str, Any]:
    """Solr parameters for the documents a search's keyword query and filters match."""

    plain = req.model_copy(
        update={"facets": [], "highlight": False, "histogram": None, "mode": "keyword"}
    )
    return search_mod.build_params(plain, allowed)


@router.post("/views/table/stats", response=TableStatsOut)
def table_stats(request: HttpRequest, payload: SearchIn) -> TableStatsOut:
    """Column statistics for the table view: size, pages and dates over every match, with the
    type and language breakdown. In semantic and hybrid modes they describe the keyword
    matches of the same words and filters."""

    allowed = read_scope(request, payload.collection)
    params = _keyword_params(payload, allowed)
    params.update(
        rows=0,
        start=0,
        stats=True,
        facet=True,
        **{
            "stats.field": ["size_l", "page_count_i", "modified_dt", "indexed_dt"],
            "facet.field": ["content_type_group_s", "language_s"],
            "facet.mincount": 1,
            "facet.limit": 20,
        },
    )
    body = services.solr_client().select(services.solr_documents_collection(), **params)
    fields = (body.get("stats") or {}).get("stats_fields") or {}

    def number(name: str) -> NumberStats:
        s = fields.get(name) or {}
        return NumberStats(
            min=s.get("min"),
            max=s.get("max"),
            mean=s.get("mean"),
            sum=s.get("sum"),
            count=int(s.get("count") or 0),
        )

    def date(name: str) -> DateStats:
        s = fields.get(name) or {}
        return DateStats(min=s.get("min"), max=s.get("max"), count=int(s.get("count") or 0))

    flat = (body.get("facet_counts") or {}).get("facet_fields") or {}

    def facet(name: str) -> list[FacetValue]:
        values = flat.get(name) or []
        return [
            FacetValue(value=str(values[i]), count=int(values[i + 1]))
            for i in range(0, len(values) - 1, 2)
        ]

    return TableStatsOut(
        total=int((body.get("response") or {}).get("numFound") or 0),
        size=number("size_l"),
        pages=number("page_count_i"),
        modified=date("modified_dt"),
        indexed=date("indexed_dt"),
        types=facet("content_type_group_s"),
        languages=facet("language_s"),
    )


# --- entities view: every entity facet of a search ----------------------------------------------

ENTITY_FIELDS: dict[str, str] = {
    "persons_ss": "People",
    "organisations_ss": "Organisations",
    "places_ss": "Places",
    "entity_labels_ss": "Linked entities",
    "concept_labels_ss": "Concepts",
    "emails_ss": "Email addresses",
    "phones_ss": "Phone numbers",
    "urls_ss": "Links",
    "money_ss": "Amounts",
    "hashtags_ss": "Hashtags",
}


class EntityFacetOut(Schema):
    field: str
    label: str
    values: list[FacetValue]


def _entity_label(field: str) -> str:
    if field in ENTITY_FIELDS:
        return ENTITY_FIELDS[field]
    if field.endswith("_entities_ss"):
        name = field.removeprefix("x_").removesuffix("_entities_ss").replace("_", " ")
        return f"{name.capitalize()} (found)"
    if field.endswith("_paths"):
        return field.removesuffix("_paths").replace("-", " ").replace("_", " ").capitalize()
    return field


@router.post("/views/entities", response=list[EntityFacetOut])
def entities(request: HttpRequest, payload: SearchIn, limit: int = 30) -> list[EntityFacetOut]:
    """The entities view: each entity, concept and pattern facet of a search, top values first."""

    allowed = read_scope(request, payload.collection)
    req = payload.model_copy(
        update={
            "facets": list(ENTITY_FIELDS),
            "facet_limit": max(1, min(limit, 100)),
            "page": 1,
            "page_size": 1,
            "highlight": False,
            "histogram": None,
        }
    )
    out = search_mod.search(
        services.solr_client(), services.solr_documents_collection(), req, allowed
    )
    order = list(ENTITY_FIELDS)
    wanted = [
        f
        for f in out.facets
        if out.facets[f] and (f in ENTITY_FIELDS or f.endswith(("_entities_ss", "_paths")))
    ]
    wanted.sort(key=lambda f: (order.index(f) if f in order else len(order), f))
    return [EntityFacetOut(field=f, label=_entity_label(f), values=out.facets[f]) for f in wanted]


# --- saved searches with their newest matches (the in-app alert list) ---------------------------


class AlertHit(Schema):
    id: str
    title: str
    collection: str
    indexed_at: str | None


class AlertOut(Schema):
    id: int
    name: str
    alert: bool
    total: int | None
    last_total: int
    last_checked_at: str | None
    newest: list[AlertHit]
    error: str = ""


def _newest(request: HttpRequest, query: dict[str, Any], rows: int) -> tuple[int, list[SearchHit]]:
    """A saved query's total and newest matches, read as the caller (403 if no longer readable)."""

    try:
        base = SearchIn(**query)
    except ValidationError as exc:
        raise HttpError(422, f"not a valid search: {str(exc)[:200]}") from exc
    req = base.model_copy(
        update={
            "page": 1,
            "page_size": max(1, min(rows, 100)),
            "sort": "indexed_dt desc",
            "facets": [],
            "histogram": None,
            "highlight": True,
        }
    )
    allowed = read_scope(request, req.collection)
    res = search_mod.search(
        services.solr_client(), services.solr_documents_collection(), req, allowed
    )
    return res.total, res.hits


@router.get("/views/alerts", response=list[AlertOut])
def alerts(request: HttpRequest, newest: int = 5) -> list[AlertOut]:
    """The caller's saved searches, alerting ones first, each with its newest matches."""

    user = _user(request)
    out: list[AlertOut] = []
    for s in sorted(SavedSearch.objects.filter(user=user), key=lambda s: (not s.alert, s.name)):
        total: int | None = None
        hits: list[SearchHit] = []
        error = ""
        try:
            total, hits = _newest(request, s.query, newest)
        except HttpError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - a malformed saved query must not hide the rest
            error = logged_detail(f"saved search {s.id} cannot run", exc)
        out.append(
            AlertOut(
                id=s.id,
                name=s.name,
                alert=s.alert,
                total=total,
                last_total=s.last_total,
                last_checked_at=s.last_checked_at.isoformat() if s.last_checked_at else None,
                newest=[
                    AlertHit(
                        id=h.id, title=h.title, collection=h.collection, indexed_at=h.indexed_at
                    )
                    for h in hits
                ],
                error=error,
            )
        )
    return out


# --- graph: co-occurrence within a search, and the documents behind an edge ---------------------

_KEYS = {
    "Document": "id",
    "Concept": "iri",
    "Entity": "key",
    "Source": "uri",
    "Collection": "slug",
    "Pack": "key",
}
_LINKS = "TAGGED|MENTIONS|CURATED|FROM|IN"


def _node_ref(node_id: str) -> tuple[str, str, str]:
    kind, _, key = node_id.partition(":")
    if kind not in _KEYS or not key:
        raise HttpError(422, f"node ids are <kind>:<key>, kind one of {tuple(_KEYS)}")
    return kind, _KEYS[kind], key


class CooccurrenceIn(Schema):
    node: str | None = None  # a Concept: or Entity: id; none for the whole search
    collection: str | None = None
    search: SearchIn | None = None  # limit to the documents this search matches (top 500)
    kinds: list[str] = ["Concept", "Entity"]
    nodes: int = 40
    edges: int = 150
    min_docs: int = 1


class EdgeIn(Schema):
    a: str
    b: str
    collection: str | None = None
    search: SearchIn | None = None
    limit: int = 50


class EdgeDocument(Schema):
    id: str
    title: str
    collection: str


class EdgeOut(Schema):
    documents: list[EdgeDocument]
    more: bool


def _scope(
    request: HttpRequest, collection: str | None, search: SearchIn | None
) -> tuple[str, dict[str, Any]]:
    """A Cypher condition on `d` (a Document) for the readable collections, the collection
    and the documents a search matches, with its parameters."""

    if search is not None and collection and search.collection not in (None, collection):
        raise HttpError(422, "the search names another collection")
    collection = collection or (search.collection if search else None)
    allowed = read_scope(request, collection)
    clauses: list[str] = []
    params: dict[str, Any] = {"allowed": allowed, "collection": collection, "ids": None}
    if allowed is not None:
        clauses.append("d.collection IN $allowed")
    if collection:
        clauses.append("d.collection = $collection")
    if search is not None:
        solr = _keyword_params(search.model_copy(update={"collection": collection}), allowed)
        solr.update(rows=500, start=0, fl="id")
        body = services.solr_client().select(services.solr_documents_collection(), **solr)
        params["ids"] = [str(doc["id"]) for doc in (body.get("response") or {}).get("docs", [])]
        clauses.append("d.id IN $ids")
    return (" AND ".join(clauses) or "true"), params


@router.post("/views/graph", response=graph.GraphOut)
def cooccurrence(request: HttpRequest, payload: CooccurrenceIn) -> graph.GraphOut:
    """Concepts and entities that occur in the same documents, weighted by how many.

    With `node`, its co-occurring neighbours; without, the network of the most
    frequent concepts and entities. `search` limits both to the documents that
    search matches (its keyword query and filters), which is how search filters
    reach the graph. Each edge is `CO_OCCURS` with `properties.docs`.
    """

    where, params = _scope(request, payload.collection, payload.search)
    kinds = [k for k in payload.kinds if k in ("Concept", "Entity")] or ["Concept", "Entity"]
    params.update(
        kinds=kinds,
        nodes=max(2, min(payload.nodes, 200)),
        edges=max(1, min(payload.edges, 1000)),
        min_docs=max(1, payload.min_docs),
    )
    if payload.node:
        kind, prop, key = _node_ref(payload.node)
        if kind not in ("Concept", "Entity"):
            raise HttpError(422, "node must be a Concept or an Entity")
        params["key"] = key
        cypher = (
            f"MATCH (n:{kind} {{{prop}: $key}})<-[:TAGGED|MENTIONS]-(d:Document)"
            f"-[:TAGGED|MENTIONS]->(x) "
            f"WHERE x <> n AND any(l IN labels(x) WHERE l IN $kinds) AND {where} "
            "WITH n, x, count(DISTINCT d) AS shared WHERE shared >= $min_docs "
            "RETURN n AS a, x AS b, shared ORDER BY shared DESC LIMIT $edges"
        )
    else:
        cypher = (
            "MATCH (d:Document)-[:TAGGED|MENTIONS]->(x) "
            f"WHERE any(l IN labels(x) WHERE l IN $kinds) AND {where} "
            "WITH x, collect(DISTINCT d.id) AS docs "
            "ORDER BY size(docs) DESC LIMIT $nodes "
            "WITH collect({x: x, docs: docs}) AS top "
            "UNWIND top AS a UNWIND top AS b "
            "WITH a, b WHERE elementId(a.x) < elementId(b.x) "
            "WITH a, b, size([i IN a.docs WHERE i IN b.docs]) AS shared "
            "WHERE shared >= $min_docs "
            "RETURN a.x AS a, b.x AS b, shared ORDER BY shared DESC LIMIT $edges"
        )
    nodes: dict[str, graph.Node] = {}
    edges: list[graph.Edge] = []
    for row in graph._raw(cypher, **params):  # noqa: SLF001 - raw node objects are needed
        a, b = graph._node(row["a"]), graph._node(row["b"])  # noqa: SLF001
        nodes.setdefault(a.id, a)
        nodes.setdefault(b.id, b)
        edges.append(
            graph.Edge(
                source=a.id, target=b.id, type="CO_OCCURS", properties={"docs": int(row["shared"])}
            )
        )
    return graph.GraphOut(nodes=list(nodes.values()), edges=edges)


@router.post("/views/graph/edge", response=EdgeOut)
def edge_documents(request: HttpRequest, payload: EdgeIn) -> EdgeOut:
    """The documents behind an edge: for two concepts or entities, the documents where both
    occur; for a document and a node, the documents that share that node."""

    ka, pa, key_a = _node_ref(payload.a)
    kb, pb, key_b = _node_ref(payload.b)
    if ka == "Document" and kb == "Document":
        raise HttpError(422, "an edge between two documents has no shared node")
    where, params = _scope(request, payload.collection, payload.search)
    limit = max(1, min(payload.limit, 500))
    params["limit"] = limit + 1
    if ka == "Document" or kb == "Document":
        kind, prop, key = (kb, pb, key_b) if ka == "Document" else (ka, pa, key_a)
        params["key"] = key
        match = f"MATCH (x:{kind} {{{prop}: $key}})-[:{_LINKS}]-(d:Document)"
    else:
        params.update(a_key=key_a, b_key=key_b)
        match = (
            f"MATCH (a:{ka} {{{pa}: $a_key}})-[:{_LINKS}]-(d:Document)"
            f"-[:{_LINKS}]-(b:{kb} {{{pb}: $b_key}})"
        )
    cypher = (
        f"{match} WHERE {where} "
        "RETURN DISTINCT d.id AS id, coalesce(d.title, d.uri, d.id) AS title, "
        "d.collection AS collection ORDER BY title LIMIT $limit"
    )
    rows = graph._raw(cypher, **params)  # noqa: SLF001
    docs = [
        EdgeDocument(
            id=str(r["id"]),
            title=str(r["title"] or r["id"]),
            collection=str(r["collection"] or ""),
        )
        for r in rows[:limit]
    ]
    return EdgeOut(documents=docs, more=len(rows) > limit)


# --- feeds --------------------------------------------------------------------------------------

FEED_AUTH = [feeds.FeedTokenAuth(), *AUTH]


class FeedTokenOut(Schema):
    token: str | None


@router.get("/feeds/token", response=FeedTokenOut, tags=["feeds"])
def get_feed_token(request: HttpRequest) -> FeedTokenOut:
    """The caller's feed token, if they have one."""

    return FeedTokenOut(token=feeds.current_feed_token(_user(request)))


@router.post("/feeds/token", response={201: FeedTokenOut}, tags=["feeds"])
def new_feed_token(request: HttpRequest) -> Status[FeedTokenOut]:
    """A new feed token; feed URLs with the previous one stop working."""

    return Status(201, FeedTokenOut(token=feeds.issue_feed_token(_user(request))))


@router.delete("/feeds/token", response={204: None}, tags=["feeds"])
def revoke_feed_token(request: HttpRequest) -> Status[None]:
    feeds.revoke_feed_token(_user(request))
    return Status(204, None)


def _format(fmt: str) -> str:
    if fmt not in ("atom", "rss"):
        raise HttpError(422, "format is atom or rss")
    return fmt


@router.get("/feeds/saved/{saved_id}", auth=FEED_AUTH, tags=["feeds"])
def saved_search_feed(
    request: HttpRequest, saved_id: int, format: str = "atom", limit: int = 20
) -> HttpResponse:
    """A saved search as a feed, newest documents first. Read with `?token=<feed token>`
    (or a session or bearer token), as the search's owner."""

    fmt = _format(format)
    saved = get_object_or_404(SavedSearch, pk=saved_id, user=_user(request))
    _, hits = _newest(request, saved.query, limit)
    return feeds.render(
        request,
        title=f"Semascope: {saved.name}",
        link="/",
        description=f"Newest documents matching the saved search {saved.name!r}",
        entries=[(h, [saved.name]) for h in hits],
        fmt=fmt,
    )


@router.get("/feeds/search", auth=FEED_AUTH, tags=["feeds"])
def search_feed(
    request: HttpRequest,
    q: str = "*:*",
    collection: str | None = None,
    format: str = "atom",
    limit: int = 20,
) -> HttpResponse:
    """Any keyword search as a feed: `q`, `collection` and `filter=<field>:<value>` (repeat)."""

    fmt = _format(format)
    filters: dict[str, list[str]] = {}
    for item in request.GET.getlist("filter"):
        field, sep, value = item.partition(":")
        if not sep or not field or not value:
            raise HttpError(422, "filter is <field>:<value>")
        filters.setdefault(field, []).append(value)
    query = {"q": q, "collection": collection, "filters": filters}
    _, hits = _newest(request, query, limit)
    return feeds.render(
        request,
        title=f"Semascope: {q}",
        link="/",
        description=f"Newest documents matching {q!r}",
        entries=[(h, []) for h in hits],
        fmt=fmt,
    )


@router.get("/feeds/alerts", auth=FEED_AUTH, tags=["feeds"])
def alerts_feed(request: HttpRequest, format: str = "atom", limit: int = 50) -> HttpResponse:
    """Every saved search with alerts on, merged into one feed, newest first; each entry
    names the searches it matched as categories. Searches the owner can no longer read
    are skipped."""

    fmt = _format(format)
    user = _user(request)
    cap = max(1, min(limit, 100))
    found: dict[str, tuple[SearchHit, list[str]]] = {}
    for saved in SavedSearch.objects.filter(user=user, alert=True):
        try:
            _, hits = _newest(request, saved.query, cap)
        except HttpError:
            continue
        for h in hits:
            entry = found.setdefault(h.id, (h, []))
            entry[1].append(saved.name)
    entries = sorted(found.values(), key=lambda e: e[0].indexed_at or "", reverse=True)[:cap]
    return feeds.render(
        request,
        title=f"Semascope alerts for {user.get_username()}",
        link="/alerts",
        description="Newest documents matching your saved searches with alerts on",
        entries=entries,
        fmt=fmt,
    )
