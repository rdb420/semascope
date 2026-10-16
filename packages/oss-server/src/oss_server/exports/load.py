"""What the export routes need from the request and the database, shared by RDF and W3C."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.http import HttpRequest

from oss_server.catalogue.models import Collection, Document
from oss_server.exports import ids, w3c


def site_of(request: HttpRequest) -> str:
    """The deployment's public base URL, as the browser reached it (Caddy forwards the
    host and the scheme)."""

    return request.build_absolute_uri("/").rstrip("/")


def secret_lookup() -> Callable[[Document], str | None]:
    """The pseudonymisation secret of each document's collection (None when it does not
    pseudonymise), computed once per collection."""

    from oss_server.pipeline.tasks import pseudonymise_secret

    cache: dict[int, str | None] = {}

    def secret(doc: Document) -> str | None:
        coll: Collection = doc.collection
        if coll.pk not in cache:
            cache[coll.pk] = pseudonymise_secret(coll)
        return cache[coll.pk]

    return secret


def rdf_annotations(
    site: str, secret: Callable[[Document], str | None], *, history: bool = False
) -> Callable[[list[Document]], dict[str, list[dict[str, Any]]]]:
    """Notes and decisions in force per document, as Web Annotation dicts for the RDF export."""

    def load(docs: list[Document]) -> dict[str, list[dict[str, Any]]]:
        qs = Document.objects.filter(pk__in=[d.id for d in docs])
        items = w3c.annotations_for(
            w3c.notes_query(qs),
            w3c.decisions_query(qs, history=history),
            site=site,
            source=lambda d: ids.doc_iri(d.id),
            secret=secret,
        )
        out: dict[str, list[dict[str, Any]]] = {}
        by_scope = {ids.page_url(site, d.id): d.id for d in docs}
        for item in items:
            doc_id = by_scope.get(str(item["target"].get("scope", "")))
            if doc_id:
                out.setdefault(doc_id, []).append(item)
        return out

    return load
