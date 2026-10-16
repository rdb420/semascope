"""Search extensions: saved searches and alerts, list search, CSV export, result-set terms."""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import datetime
from functools import cache
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import ValidationError

from oss_server.api import search as search_mod
from oss_server.api.access import read_scope
from oss_server.api.schemas import DateRange, SearchIn
from oss_server.catalogue.models import Document, SavedSearch
from oss_server.knowledge.synonyms import fold, stopwords
from oss_server.services import solr_client, solr_documents_collection

router = Router(tags=["search"])
# a word of the terms view: three or more letters of any script (no digits, no punctuation)
_WORD = re.compile(r"[^\W\d_]{3,}")


class SavedSearchIn(Schema):
    name: str
    query: dict[str, Any]
    alert: bool = False


class SavedSearchOut(Schema):
    id: int
    name: str
    query: dict[str, Any]
    alert: bool
    last_checked_at: datetime | None
    last_total: int
    created_at: datetime


class ListSearchIn(Schema):
    names: list[str]
    collection: str | None = None
    rows_per_name: int = 3
    # only documents that pass these, as in a search (LIST-04)
    filters: dict[str, list[str]] = {}
    exclude: dict[str, list[str]] = {}
    dates: DateRange | None = None
    # how far a name may be loosened when the stricter way finds nothing (LIST-02)
    methods: list[str] = ["phrase", "words", "fuzzy"]


def _user(request: HttpRequest) -> Any:
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        raise HttpError(401, "sign in")
    return user


def _out(s: SavedSearch) -> SavedSearchOut:
    return SavedSearchOut(
        id=s.id,
        name=s.name,
        query=s.query,
        alert=s.alert,
        last_checked_at=s.last_checked_at,
        last_total=s.last_total,
        created_at=s.created_at,
    )


@router.get("/saved", response=list[SavedSearchOut])
def list_saved(request: HttpRequest) -> list[SavedSearchOut]:
    return [_out(s) for s in SavedSearch.objects.filter(user=_user(request))]


@router.post("/saved", response={201: SavedSearchOut})
def save_search(request: HttpRequest, payload: SavedSearchIn) -> tuple[int, SavedSearchOut]:
    user = _user(request)
    try:
        query = SearchIn(**payload.query)
    except ValidationError as exc:
        raise HttpError(422, str(exc)[:300]) from exc
    read_scope(request, query.collection)
    s, _ = SavedSearch.objects.update_or_create(
        user=user,
        name=payload.name,
        defaults={"query": payload.query, "alert": payload.alert},
    )
    return 201, _out(s)


@router.delete("/saved/{saved_id}", response={204: None})
def delete_saved(request: HttpRequest, saved_id: int) -> tuple[int, None]:
    get_object_or_404(SavedSearch, pk=saved_id, user=_user(request)).delete()
    return 204, None


LIST_METHODS = ("phrase", "words", "fuzzy")
_LIST_WORD = re.compile(r"\w+")


def list_queries(name: str) -> dict[str, tuple[str, str | None, bool]]:
    """The queries a list-search name cascades through: (query, operator, stemming).

    `phrase` is the name as one phrase; `words` every word of it, in any order, other
    word forms included; `fuzzy` every word with one typing error allowed. The name is
    data: the phrase is one quoted term, each word is quoted or reduced to letters and
    digits, so nothing in it is ever read as query syntax.
    """

    words = name.split()
    plain = [w for w in _LIST_WORD.findall(name) if len(w) > 1]
    out: dict[str, tuple[str, str | None, bool]] = {
        "phrase": (search_mod._escape(name), None, True),
    }
    if len(words) > 1:
        out["words"] = (" ".join(search_mod._escape(w) for w in words), "all", True)
    if plain:
        fuzzy = " ".join(f"{w.lower()}~1" if len(w) >= 4 else search_mod._escape(w) for w in plain)
        out["fuzzy"] = (fuzzy, "all", False)
    return out


@router.post("/list", response=dict[str, Any])
def list_search(request: HttpRequest, payload: ListSearchIn) -> dict[str, Any]:
    """Batch search, one name at a time: its count, best score, a few hits and the match
    method that found it (the strictest that finds anything: phrase, then all its words,
    then the words with one typing error each)."""

    if len(payload.names) > 500:
        raise HttpError(422, "at most 500 names per call")
    methods = [m for m in LIST_METHODS if m in payload.methods] or ["phrase"]
    allowed = read_scope(request, payload.collection)
    client = solr_client()
    out: dict[str, Any] = {}
    for name in payload.names:
        name = name.strip()
        if not name or name in out:
            continue
        found: dict[str, Any] = {"total": 0, "hits": [], "method": None, "best_score": None}
        queries = list_queries(name)
        for method in methods:
            if method not in queries:
                continue
            q, operator, stemming = queries[method]
            req = SearchIn(
                q=q,
                collection=payload.collection,
                filters=payload.filters,
                exclude=payload.exclude,
                dates=payload.dates,
                facets=[],
                page_size=max(1, min(payload.rows_per_name, 20)),
                highlight=True,
                operator=operator,
                stemming=stemming,
            )
            res = search_mod.search(client, solr_documents_collection(), req, allowed)
            if res.total:
                found = {
                    "total": res.total,
                    "hits": [h.model_dump() for h in res.hits],
                    "method": method,
                    "best_score": res.hits[0].score if res.hits else None,
                }
                break
        out[name] = found
    return out


@router.post("/export.csv")
def export_csv(request: HttpRequest, payload: SearchIn) -> HttpResponse:
    """The first 10,000 hits of a search as CSV (id, title, uri, type, language, dates)."""

    allowed = read_scope(request, payload.collection)
    client = solr_client()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "id",
            "title",
            "uri",
            "collection",
            "content_type_group",
            "media_type",
            "language",
            "pages",
            "size",
            "modified_at",
            "indexed_at",
        ]
    )
    page = 1
    written = 0
    while written < 10000:
        req = payload.model_copy(
            update={"page": page, "page_size": 100, "highlight": False, "facets": []}
        )
        res = search_mod.search(client, solr_documents_collection(), req, allowed)
        for h in res.hits:
            writer.writerow(
                [
                    h.id,
                    h.title,
                    h.uri,
                    h.collection,
                    h.content_type_group,
                    h.media_type,
                    h.language,
                    h.page_count,
                    h.size,
                    h.modified_at,
                    h.indexed_at,
                ]
            )
            written += 1
        if len(res.hits) < 100:
            break
        page += 1
    resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="semascope-search.csv"'
    return resp


@router.post("/terms", response=list[dict[str, Any]])
def terms(
    request: HttpRequest, payload: SearchIn, top: int = 40, sample: int = 30
) -> list[dict[str, Any]]:
    """The words that stand out in the top hits of a search against the background of the
    readable collections searched: a word scores by how much more often the hits contain it
    than the background's documents do (`score`; O-32). `count` is its occurrences in the
    hits, `docs` the hits that contain it, `background` the documents of the background."""

    allowed = read_scope(request, payload.collection)
    client = solr_client()
    req = payload.model_copy(
        update={"page": 1, "page_size": max(1, min(sample, 100)), "highlight": False, "facets": []}
    )
    res = search_mod.search(client, solr_documents_collection(), req, allowed)
    ids = [h.id for h in res.hits]
    if not ids:
        return []
    sampled_docs = list(Document.objects.filter(pk__in=ids).only("text", "language"))
    # the stop words of the languages of the hits (English always)
    stop = _stopwords(frozenset({"en", *((d.language or "")[:2] for d in sampled_docs)}))
    counts: Counter[str] = Counter()
    docs_with: Counter[str] = Counter()
    for doc in sampled_docs:
        seen = set()
        for w in _WORD.findall(doc.text[:200000]):
            w = fold(w.lower())
            if w in stop:
                continue
            counts[w] += 1
            seen.add(w)
        docs_with.update(seen)
    candidates = [w for w, n in counts.most_common(BACKGROUND_CANDIDATES) if docs_with[w] > 0]
    total, background = _background(client, candidates, allowed, payload.collection)
    sampled = len(ids)

    def score(w: str) -> float:
        fg = docs_with[w] / sampled
        bg = (background.get(w, 0) + 1) / (total + 1)
        return (fg - bg) * (fg / bg) if fg > bg else 0.0

    ranked = sorted(candidates, key=lambda w: (-score(w), -counts[w], w))
    return [
        {
            "term": w,
            "count": counts[w],
            "docs": docs_with[w],
            "background": background.get(w, 0),
            "score": round(score(w), 4),
        }
        for w in ranked[: max(1, min(top, 200))]
    ]


# the most frequent words of the hits that are weighed against the background
BACKGROUND_CANDIDATES = 200


def _background(
    client: Any, words: list[str], allowed: list[str] | None, collection: str | None
) -> tuple[int, dict[str, int]]:
    """Documents in the background (the readable collections searched), and how many of
    them hold each word: one Solr request, a term query per word (the words are letters
    only and already in the index's form, so no analysis and no synonyms apply)."""

    fq = search_mod.collection_filter(allowed)
    if collection:
        fq.append(f"collection_s:{search_mod._escape(collection)}")
    queries = {f"{{!term f=text_txt}}{w}": w for w in words}
    body = client.select(
        solr_documents_collection(),
        q="*:*",
        rows=0,
        fq=fq,
        facet=True,
        **{"facet.query": list(queries)},
    )
    total = int(body.get("response", {}).get("numFound", 0))
    found = body.get("facet_counts", {}).get("facet_queries", {}) or {}
    return total, {queries[k]: int(v) for k, v in found.items() if k in queries}


@cache
def _stopwords(languages: frozenset[str] = frozenset({"en"})) -> frozenset[str]:
    """The stop words of these languages from the documents configset, plus `_EXTRA_STOP`
    (English, archaic English included)."""

    try:
        return frozenset({fold(w) for w in stopwords(languages)} | _EXTRA_STOP)
    except FileNotFoundError:
        return frozenset(_EXTRA_STOP)


_EXTRA_STOP = set(
    """
    a about above after again against all am an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further
    had has have having he her here hers herself him himself his how i if in into is it its
    itself just let like made make many may me might more most much must my myself no nor not
    now of off on once one only or other our ours ourselves out over own same shall she should
    so some such than that the their theirs them themselves then there these they this those
    through to too under until up upon us very was we were what when where which while who whom
    why will with would you your yours yourself yourselves thou thee thy thine ye hath doth
    unto thus whence whither wherefore art hast shalt wilt said say says saw see seen come came
    went go gone get got give given take took put set tell told two three first last long great
    little old new good well also even ever never still yet again back away day days man men
    way thing things time
    """.split()
)
