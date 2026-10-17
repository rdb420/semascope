"""The documents of a search, in the search's order, checked against the read scope twice.

Solr filters the search to the readable collections; every document it returns
is then looked up in Postgres and kept only if its collection is in scope, so a
stale index can never widen what a caller sees (the rule the RDF export follows).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from django.db.models import QuerySet
from django.db.models.functions import Substr

from oss_server import services
from oss_server.api import search as search_mod
from oss_server.api.schemas import SearchIn
from oss_server.catalogue.models import Document

PAGE = 100


def scope_of(payload: SearchIn, allowed: list[str] | None) -> list[str] | None:
    """The collections the documents may come from (None: every collection)."""

    return [payload.collection] if payload.collection else allowed


def hit_pages(
    payload: SearchIn, allowed: list[str] | None, limit: int, page_size: int = PAGE
) -> Iterator[list[Any]]:
    """The search's hits, a page at a time, at most `limit` of them."""

    client = services.solr_client()
    sent = 0
    page = 1
    while sent < limit:
        req = payload.model_copy(
            update={"page": page, "page_size": page_size, "highlight": False, "facets": []}
        )
        res = search_mod.search(client, services.solr_documents_collection(), req, allowed)
        hits = list(res.hits)[: limit - sent]
        if hits:
            yield hits
        sent += len(hits)
        if len(res.hits) < page_size:
            return
        page += 1


def in_scope(docs: QuerySet[Document], scope: list[str] | None) -> QuerySet[Document]:
    return docs if scope is None else docs.filter(collection__slug__in=scope)


def search_documents(
    payload: SearchIn,
    allowed: list[str] | None,
    limit: int,
    *,
    queryset: QuerySet[Document] | None = None,
) -> Iterator[Document]:
    """The documents of a search in its order, from `queryset` (default: every field)."""

    scope = scope_of(payload, allowed)
    base = queryset if queryset is not None else Document.objects.select_related("collection")
    for hits in hit_pages(payload, allowed, limit):
        wanted = [h.id for h in hits]
        rows = in_scope(base.filter(pk__in=wanted), scope).in_bulk(wanted)
        for did in wanted:
            doc = rows.get(did)
            if doc is not None:
                yield doc


def with_text_head(chars: int) -> QuerySet[Document]:
    """Documents with the first `chars` characters of their text as `head`, the full text
    and the other large fields left in the database."""

    return (
        Document.objects.select_related("collection")
        .only("id", "title", "uri", "language", "collection__slug", "collection__name")
        .annotate(head=Substr("text", 1, chars))
    )


def sample_texts(
    payload: SearchIn, allowed: list[str] | None, sample: int, chars: int
) -> list[Document]:
    """The first `sample` documents of a search, each with `head` (its text, cut)."""

    return list(search_documents(payload, allowed, sample, queryset=with_text_head(chars)))


def documents_by_id(ids: Sequence[str], scope: list[str] | None, chars: int) -> list[Document]:
    rows = in_scope(with_text_head(chars).filter(pk__in=list(ids)), scope).in_bulk(list(ids))
    return [rows[i] for i in ids if i in rows]
