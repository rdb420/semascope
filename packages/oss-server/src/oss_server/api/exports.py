"""RDF exports (parity rows ETL-EXP-04, ETL-EXP-05): one document, or a search's results,
with their tags and curator decisions, as Turtle or JSON-LD. Streamed and read-scoped."""

from __future__ import annotations

from collections.abc import Iterator

from django.http import HttpRequest, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError

from oss_server import services
from oss_server.api import search as search_mod
from oss_server.api.access import check, read_scope
from oss_server.api.schemas import SearchIn
from oss_server.catalogue.models import Document
from oss_server.exports import rdf
from oss_server.exports.load import rdf_annotations, secret_lookup, site_of

MAX_DOCUMENTS = 10000  # as the CSV export
DEFAULT_DOCUMENTS = 1000
PAGE = 100


def _format(fmt: str) -> tuple[str, str]:
    if fmt not in rdf.FORMATS:
        raise HttpError(422, f"format must be one of {', '.join(rdf.FORMATS)}")
    return rdf.FORMATS[fmt]


def _response(chunks: Iterator[str], content_type: str, filename: str) -> StreamingHttpResponse:
    resp = StreamingHttpResponse((c.encode("utf-8") for c in chunks), content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def export_document(
    request: HttpRequest, doc_id: str, format: str = "turtle"
) -> StreamingHttpResponse:
    """One document with its tags in force, notes and decisions, as Turtle or JSON-LD."""

    doc = get_object_or_404(Document.objects.select_related("collection"), pk=doc_id)
    check(request, "reader", doc.collection.slug)
    content_type, ext = _format(format)
    site = site_of(request)
    secret = secret_lookup()
    graphs = rdf.document_graphs(
        [doc], site=site, secret=secret, annotations=rdf_annotations(site, secret)
    )
    return _response(rdf.stream(graphs, format), content_type, f"semascope-{doc.id}.{ext}")


def export_search(
    request: HttpRequest, payload: SearchIn, format: str = "turtle", limit: int = DEFAULT_DOCUMENTS
) -> StreamingHttpResponse:
    """The hits of a search (up to `limit`, at most 10,000) with their tags, notes and
    decisions, as Turtle or JSON-LD, in the search's order."""

    content_type, ext = _format(format)
    allowed = read_scope(request, payload.collection)
    limit = max(1, min(limit, MAX_DOCUMENTS))
    site = site_of(request)
    secret = secret_lookup()
    scope = [payload.collection] if payload.collection else allowed

    def documents() -> Iterator[Document]:
        client = services.solr_client()
        sent = 0
        page = 1
        while sent < limit:
            req = payload.model_copy(
                update={"page": page, "page_size": PAGE, "highlight": False, "facets": []}
            )
            res = search_mod.search(client, services.solr_documents_collection(), req, allowed)
            wanted = [h.id for h in res.hits][: limit - sent]
            rows = Document.objects.select_related("collection").in_bulk(wanted)
            for did in wanted:
                doc = rows.get(did)
                # Solr and Postgres agree on scope; check again so a stale index cannot widen it
                if doc is not None and (scope is None or doc.collection.slug in scope):
                    yield doc
            sent += len(wanted)
            if len(res.hits) < PAGE:
                return
            page += 1

    graphs = rdf.document_graphs(
        documents(), site=site, secret=secret, annotations=rdf_annotations(site, secret)
    )
    return _response(rdf.stream(graphs, format), content_type, f"semascope-search.{ext}")


def mount(router: Router) -> None:
    router.get("/export/documents/{doc_id}", tags=["export"])(export_document)
    router.post("/export/search", tags=["export"])(export_search)
