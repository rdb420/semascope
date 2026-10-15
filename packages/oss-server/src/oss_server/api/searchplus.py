"""Search extensions: saved searches and alerts, list search, CSV export, result-set terms."""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import datetime
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import ValidationError

from oss_server.api import search as search_mod
from oss_server.api.schemas import SearchIn
from oss_server.catalogue.models import Document, SavedSearch
from oss_server.services import solr_client, solr_documents_collection

router = Router(tags=["search"])
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")


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
    try:
        SearchIn(**payload.query)
    except ValidationError as exc:
        raise HttpError(422, str(exc)[:300]) from exc
    s, _ = SavedSearch.objects.update_or_create(
        user=_user(request),
        name=payload.name,
        defaults={"query": payload.query, "alert": payload.alert},
    )
    return 201, _out(s)


@router.delete("/saved/{saved_id}", response={204: None})
def delete_saved(request: HttpRequest, saved_id: int) -> tuple[int, None]:
    get_object_or_404(SavedSearch, pk=saved_id, user=_user(request)).delete()
    return 204, None


@router.post("/list", response=dict[str, Any])
def list_search(request: HttpRequest, payload: ListSearchIn) -> dict[str, Any]:
    """Batch search: one phrase query per name, with counts and a few hits each."""

    if len(payload.names) > 500:
        raise HttpError(422, "at most 500 names per call")
    client = solr_client()
    out: dict[str, Any] = {}
    for name in payload.names:
        name = name.strip()
        if not name:
            continue
        req = SearchIn(
            q=f'"{name}"',
            collection=payload.collection,
            facets=[],
            page_size=max(1, min(payload.rows_per_name, 20)),
            highlight=True,
        )
        res = search_mod.search(client, solr_documents_collection(), req)
        out[name] = {"total": res.total, "hits": [h.model_dump() for h in res.hits]}
    return out


@router.post("/export.csv")
def export_csv(request: HttpRequest, payload: SearchIn) -> HttpResponse:
    """The first 10,000 hits of a search as CSV (id, title, uri, type, language, dates)."""

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
        res = search_mod.search(client, solr_documents_collection(), req)
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
    """The words that stand out in the top hits of a search, against the collection's background."""

    client = solr_client()
    req = payload.model_copy(
        update={"page": 1, "page_size": max(1, min(sample, 100)), "highlight": False, "facets": []}
    )
    res = search_mod.search(client, solr_documents_collection(), req)
    ids = [h.id for h in res.hits]
    if not ids:
        return []
    stop = _stopwords()
    counts: Counter[str] = Counter()
    docs_with: Counter[str] = Counter()
    for doc in Document.objects.filter(pk__in=ids).only("text"):
        words = [w.lower() for w in _WORD.findall(doc.text[:200000])]
        seen = set()
        for w in words:
            if w in stop:
                continue
            counts[w] += 1
            seen.add(w)
        docs_with.update(seen)
    return [
        {"term": w, "count": n, "docs": docs_with[w]}
        for w, n in counts.most_common(max(1, min(top, 200)))
    ]


def _stopwords() -> set[str]:
    from pathlib import Path

    for candidate in (
        Path(__file__).resolve().parents[4]
        / "deploy"
        / "solr"
        / "configsets"
        / "documents"
        / "conf"
        / "lang"
        / "stopwords_en.txt",
        Path("/app/deploy/solr/configsets/documents/conf/lang/stopwords_en.txt"),
    ):
        if candidate.exists():
            return {
                line.strip().lower()
                for line in candidate.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            } | _EXTRA_STOP
    return _EXTRA_STOP


_EXTRA_STOP = {
    "thou",
    "thee",
    "thy",
    "thine",
    "hath",
    "unto",
    "shall",
    "said",
    "him",
    "his",
    "her",
    "they",
    "them",
    "their",
    "will",
    "one",
    "all",
    "upon",
    "which",
    "from",
    "with",
    "were",
    "have",
    "had",
    "not",
    "but",
    "and",
    "the",
    "that",
    "this",
    "for",
    "are",
    "was",
    "you",
    "your",
    "then",
    "there",
    "when",
    "who",
    "what",
    "into",
    "out",
    "thus",
    "also",
    "may",
    "men",
    "man",
    "come",
    "came",
    "went",
    "let",
    "now",
    "yet",
    "ye",
    "did",
    "doth",
    "again",
    "against",
    "among",
    "before",
    "after",
    "over",
    "under",
    "could",
    "would",
    "should",
    "being",
    "been",
    "more",
    "most",
    "than",
    "those",
    "these",
    "such",
    "very",
    "even",
    "ever",
    "never",
    "still",
    "well",
    "much",
    "many",
    "some",
    "any",
    "every",
    "each",
    "where",
    "whom",
    "whose",
    "while",
    "about",
    "because",
    "through",
    "between",
    "both",
    "own",
    "same",
    "other",
    "another",
    "himself",
    "herself",
    "themselves",
    "itself",
}
