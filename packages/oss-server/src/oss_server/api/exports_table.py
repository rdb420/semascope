"""Table and graph exports (parity rows ETL-EXP-08, DM-20, ANL-17, ETL-EXP-04):

- `POST /export/documents`  a search's documents as CSV, JSON Lines or Parquet
- `POST /export/entities`   the concepts and entities in force on those documents, one row
                            per document and tag, in the same formats
- `GET  /export/graph`      a collection's documents with their concepts and entities as
                            RDF (Turtle, JSON-LD or N-Quads)

All three stream and stop at 10,000 documents. The search exports read the
search's hits in the readable collections and check each document's collection
again in Postgres; the graph export needs reader on its collection.
"""

from __future__ import annotations

from collections.abc import Iterator

from django.http import HttpRequest, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError

from oss_server.analysis import hits
from oss_server.api.access import check, read_scope
from oss_server.api.schemas import SearchIn
from oss_server.audit.log import record
from oss_server.catalogue.models import Collection, Document
from oss_server.exports import graph as graph_export
from oss_server.exports import rdf, tabular
from oss_server.exports.load import secret_lookup, site_of

MAX_DOCUMENTS = 10000
DEFAULT_DOCUMENTS = 1000
MAX_TEXT_CHARS = 1_000_000
MAX_ENTITY_ROWS = 1_000_000


def _format(fmt: str) -> tuple[str, str]:
    if fmt not in tabular.FORMATS:
        raise HttpError(422, f"format must be one of {', '.join(tabular.FORMATS)}")
    return tabular.FORMATS[fmt]


def _response(chunks: Iterator[bytes], content_type: str, filename: str) -> StreamingHttpResponse:
    resp = StreamingHttpResponse(chunks, content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def export_documents(
    request: HttpRequest,
    payload: SearchIn,
    format: str = "csv",
    limit: int = DEFAULT_DOCUMENTS,
    text_chars: int = 0,
) -> StreamingHttpResponse:
    """The documents of a search (up to `limit`, at most 10,000), one row each, in the
    search's order: catalogue fields, concepts in force, entity count, and with
    `text_chars` the start of the text (at most 1,000,000 characters)."""

    content_type, ext = _format(format)
    allowed = read_scope(request, payload.collection)
    limit = max(1, min(limit, MAX_DOCUMENTS))
    text_chars = max(0, min(text_chars, MAX_TEXT_CHARS))
    site = site_of(request)
    columns = tabular.document_columns(bool(text_chars))
    rows = (
        tabular.document_row(doc, site=site, text_chars=text_chars)
        for doc in hits.search_documents(payload, allowed, limit)
    )
    record(
        "export.documents",
        collection=payload.collection or "",
        detail={"q": payload.q, "filters": payload.filters, "format": format, "limit": limit},
    )
    stream = tabular.STREAMS[format](tabular.batched(rows), columns)
    return _response(stream, content_type, f"semascope-documents.{ext}")


def export_entities(
    request: HttpRequest, payload: SearchIn, format: str = "csv", limit: int = DEFAULT_DOCUMENTS
) -> StreamingHttpResponse:
    """The concepts and entities in force on a search's documents (up to `limit`, at most
    10,000 documents): one row per document and tag. Named entities of a collection that
    pseudonymises are exported by key, without their labels."""

    content_type, ext = _format(format)
    allowed = read_scope(request, payload.collection)
    limit = max(1, min(limit, MAX_DOCUMENTS))
    secret = secret_lookup()

    def rows() -> Iterator[tabular.Row]:
        sent = 0
        for doc in hits.search_documents(payload, allowed, limit):
            for row in tabular.entity_rows(doc, secret(doc)):
                if sent >= MAX_ENTITY_ROWS:
                    return
                sent += 1
                yield row

    record(
        "export.entities",
        collection=payload.collection or "",
        detail={"q": payload.q, "filters": payload.filters, "format": format, "limit": limit},
    )
    stream = tabular.STREAMS[format](tabular.batched(rows()), tabular.ENTITY_COLUMNS)
    return _response(stream, content_type, f"semascope-entities.{ext}")


def export_graph(
    request: HttpRequest, collection: str, format: str = "turtle", limit: int = MAX_DOCUMENTS
) -> StreamingHttpResponse:
    """A collection's indexed documents (up to `limit`, at most 10,000) with the concepts
    and entities they hold in force as `dcterms:subject`, the concepts' schemes and broader
    concepts, as Turtle, JSON-LD or N-Quads (one named graph: the collection)."""

    if format not in rdf.FORMATS:
        raise HttpError(422, f"format must be one of {', '.join(rdf.FORMATS)}")
    content_type, ext = rdf.FORMATS[format]
    coll = get_object_or_404(Collection, slug=collection)
    check(request, "reader", coll.slug)
    limit = max(1, min(limit, MAX_DOCUMENTS))
    record("export.graph", collection=coll.slug, detail={"format": format, "limit": limit})
    docs = (
        Document.objects.filter(collection=coll, status=Document.Status.INDEXED)
        .only("id", "title", "tags", "fields", "collection_id")
        .order_by("id")[:limit]
        .iterator(chunk_size=200)
    )
    from oss_server.pipeline.tasks import pseudonymise_secret

    graphs = graph_export.graph_batches(coll, docs, secret=pseudonymise_secret(coll))
    chunks = (c.encode("utf-8") for c in rdf.stream(graphs, format))
    return _response(chunks, content_type, f"semascope-graph-{coll.slug}.{ext}")


def mount(router: Router) -> None:
    router.post("/export/documents", tags=["export"])(export_documents)
    router.post("/export/entities", tags=["export"])(export_entities)
    router.get("/export/graph", tags=["export"])(export_graph)
