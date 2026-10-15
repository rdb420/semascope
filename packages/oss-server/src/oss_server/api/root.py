"""API routes. Every state change is a POST, PUT, PATCH or DELETE."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.db.models import Count
from django.http import FileResponse, HttpRequest
from django.shortcuts import get_object_or_404
from ninja import File, NinjaAPI, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile

from oss_core import __version__
from oss_server.api import accounts, graph, knowledge, reconcile
from oss_server.api import search as search_mod
from oss_server.api.auth import AUTH
from oss_server.api.schemas import (
    CollectionIn,
    CollectionOut,
    DocumentOut,
    DocumentTextOut,
    RunOut,
    SearchIn,
    SearchOut,
    SourceIn,
    SourceOut,
    StatsOut,
    SuggestOut,
)
from oss_server.catalogue.models import Collection, Document, Source
from oss_server.services import solr_client, solr_documents_collection

api = NinjaAPI(title="Semascope", version=__version__, auth=AUTH, urls_namespace="api")
api.add_router("/auth", accounts.router)
api.add_router("/knowledge", knowledge.router)
api.add_router("/reconcile", reconcile.router)
api.add_router("/graph", graph.router)


# --- collections ------------------------------------------------------------------------------


def _collection_out(c: Collection) -> CollectionOut:
    return CollectionOut(
        slug=c.slug,
        name=c.name,
        description=c.description,
        profile=c.profile,
        settings=c.settings,
        created_at=c.created_at,
        document_count=getattr(c, "document_count", 0),
    )


@api.get("/collections", response=list[CollectionOut], tags=["collections"])
def list_collections(request: HttpRequest) -> list[CollectionOut]:
    return [
        _collection_out(c) for c in Collection.objects.annotate(document_count=Count("documents"))
    ]


@api.post("/collections", response={201: CollectionOut}, tags=["collections"])
def create_collection(request: HttpRequest, payload: CollectionIn) -> tuple[int, CollectionOut]:
    if Collection.objects.filter(slug=payload.slug).exists():
        raise HttpError(409, f"collection {payload.slug} exists")
    c = Collection.objects.create(
        slug=payload.slug,
        name=payload.name or payload.slug,
        description=payload.description,
        profile=payload.profile,
        settings=payload.settings,
    )
    return 201, _collection_out(c)


@api.get("/collections/{slug}", response=CollectionOut, tags=["collections"])
def get_collection(request: HttpRequest, slug: str) -> CollectionOut:
    return _collection_out(
        get_object_or_404(Collection.objects.annotate(document_count=Count("documents")), slug=slug)
    )


@api.post("/collections/{slug}/reindex", response={202: dict[str, int]}, tags=["collections"])
def reindex_collection_view(request: HttpRequest, slug: str) -> tuple[int, dict[str, int]]:
    """Replay enrichment and indexing for every indexed document from stored text."""

    from oss_server.pipeline.tasks import reindex_collection

    get_object_or_404(Collection, slug=slug)
    reindex_collection.delay(slug)
    return 202, {"queued": 1}


@api.delete("/collections/{slug}", response={204: None}, tags=["collections"])
def delete_collection(request: HttpRequest, slug: str) -> tuple[int, None]:
    c = get_object_or_404(Collection, slug=slug)
    solr_client().delete_by_query(solr_documents_collection(), f'collection_s:"{slug}"')
    c.delete()
    return 204, None


# --- sources ----------------------------------------------------------------------------------


def _source_out(s: Source) -> SourceOut:
    return SourceOut(
        id=s.id,
        collection=s.collection.slug,
        name=s.name,
        connector=s.connector,
        settings=s.settings,
        enabled=s.enabled,
        schedule=s.schedule,
        status=s.status,
        last_run_at=s.last_run_at,
        last_error=s.last_error,
        document_count=getattr(s, "document_count", 0),
    )


@api.get("/collections/{slug}/sources", response=list[SourceOut], tags=["sources"])
def list_sources(request: HttpRequest, slug: str) -> list[SourceOut]:
    c = get_object_or_404(Collection, slug=slug)
    return [
        _source_out(s)
        for s in c.sources.select_related("collection").annotate(document_count=Count("documents"))
    ]


@api.post("/collections/{slug}/sources", response={201: SourceOut}, tags=["sources"])
def create_source(request: HttpRequest, slug: str, payload: SourceIn) -> tuple[int, SourceOut]:
    from oss_pipeline.registry import connectors

    c = get_object_or_404(Collection, slug=slug)
    if payload.connector not in connectors():
        raise HttpError(
            422, f"unknown connector {payload.connector}; known: {sorted(connectors())}"
        )
    s, created = Source.objects.get_or_create(
        collection=c,
        name=payload.name,
        defaults={
            "connector": payload.connector,
            "settings": payload.settings,
            "enabled": payload.enabled,
            "schedule": payload.schedule,
        },
    )
    if not created:
        raise HttpError(409, f"source {payload.name} exists in {slug}")
    return 201, _source_out(s)


@api.post("/sources/{source_id}/run", response={202: RunOut}, tags=["sources"])
def run_source(request: HttpRequest, source_id: int) -> tuple[int, RunOut]:
    from oss_server.pipeline.tasks import discover_source

    s = get_object_or_404(Source, pk=source_id)
    result = discover_source.delay(s.id)
    return 202, RunOut(source_id=s.id, task_id=str(result.id))


@api.delete("/sources/{source_id}", response={204: None}, tags=["sources"])
def delete_source(request: HttpRequest, source_id: int) -> tuple[int, None]:
    s = get_object_or_404(Source, pk=source_id)
    solr_client().delete_by_query(solr_documents_collection(), f"source_id_s:{s.id}")
    s.delete()
    return 204, None


@api.post("/collections/{slug}/upload", response={202: list[DocumentOut]}, tags=["sources"])
def upload(
    request: HttpRequest, slug: str, files: File[list[UploadedFile]]
) -> tuple[int, list[DocumentOut]]:
    """Upload one or more files into the collection's `uploads` source and queue them."""

    from oss_core.config import get_settings
    from oss_pipeline.connectors.upload import store_upload, upload_root
    from oss_server.catalogue.models import document_id
    from oss_server.pipeline.tasks import process_document

    c = get_object_or_404(Collection, slug=slug)
    data_dir = get_settings().semascope_data_dir
    source, _ = Source.objects.get_or_create(
        collection=c,
        name="uploads",
        defaults={"connector": "upload", "settings": {"path": str(upload_root(data_dir, slug))}},
    )
    out: list[DocumentOut] = []
    for f in files:
        content = f.read()
        if not content:
            continue
        path = store_upload(data_dir, slug, f.name or "upload", content)
        digest = path.parent.name
        uri = f"upload://{slug}/{digest}/{path.name}"
        doc, _ = Document.objects.update_or_create(
            id=document_id(slug, uri),
            defaults={
                "collection": c,
                "source": source,
                "uri": uri,
                "content_hash": digest,
                "size": len(content),
                "status": Document.Status.QUEUED,
                "error": "",
            },
        )
        item = {
            "uri": uri,
            "content_hash": digest,
            "size": len(content),
            "metadata": {"path": str(path)},
        }
        process_document.delay(doc.id, item)
        out.append(_document_out(doc))
    return 202, out


# --- documents --------------------------------------------------------------------------------


def _document_out(d: Document) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        collection=d.collection.slug,
        source_id=d.source_id,
        uri=d.uri,
        title=d.title,
        media_type=d.media_type,
        language=d.language,
        size=d.size,
        status=d.status,
        error=d.error,
        modified_at=d.modified_at,
        indexed_at=d.indexed_at,
        metadata=d.metadata,
        fields=d.fields,
        stage_runs=d.stage_runs,
    )


@api.get("/documents/{doc_id}", response=DocumentOut, tags=["documents"])
def get_document(request: HttpRequest, doc_id: str) -> DocumentOut:
    return _document_out(
        get_object_or_404(Document.objects.select_related("collection"), pk=doc_id)
    )


@api.get("/documents/{doc_id}/text", response=DocumentTextOut, tags=["documents"])
def get_document_text(request: HttpRequest, doc_id: str) -> DocumentTextOut:
    d = get_object_or_404(Document, pk=doc_id)
    return DocumentTextOut(id=d.id, text=d.text, pages=d.pages)


@api.get("/documents/{doc_id}/preview/{page}", tags=["documents"], url_name="preview")
def get_preview(request: HttpRequest, doc_id: str, page: int) -> FileResponse:
    """A rendered page (PDF) or thumbnail (image) as WebP."""

    d = get_object_or_404(Document, pk=doc_id)
    preview_dir = d.fields.get("preview_dir")
    if not preview_dir or page < 1:
        raise HttpError(404, "no preview")
    file = Path(str(preview_dir)) / f"{page}.webp"
    if not file.is_file():
        raise HttpError(404, "no preview for that page")
    return FileResponse(file.open("rb"), content_type="image/webp")


@api.post("/documents/{doc_id}/reindex", response={202: dict[str, str]}, tags=["documents"])
def reindex_document(request: HttpRequest, doc_id: str) -> tuple[int, dict[str, str]]:
    from oss_server.pipeline.tasks import reindex_document as task

    d = get_object_or_404(Document, pk=doc_id)
    result = task.delay(d.id)
    return 202, {"task_id": str(result.id)}


# --- search -----------------------------------------------------------------------------------


@api.post("/search", response=SearchOut, tags=["search"])
def search(request: HttpRequest, payload: SearchIn) -> SearchOut:
    return search_mod.search(solr_client(), solr_documents_collection(), payload)


class TagQueryIn(Schema):
    q: str


@api.post("/search/tag-query", response=list[dict[str, Any]], tags=["search"])
def tag_query(request: HttpRequest, payload: TagQueryIn) -> list[dict[str, Any]]:
    """Concepts and entities a query names, for facet suggestions."""

    from oss_server.api.reconcile import reconcile_one

    q = payload.q.strip()
    if not q:
        return []
    found: list[dict[str, Any]] = []
    words = q.split()
    seen: set[str] = set()
    for size in (3, 2, 1):
        for i in range(0, max(0, len(words) - size + 1)):
            phrase = " ".join(words[i : i + size])
            for cand in reconcile_one(phrase, limit=3):
                if cand["score"] >= 90 and cand["id"] not in seen:
                    seen.add(cand["id"])
                    found.append({"phrase": phrase, **cand})
    return found


def suggest(request: HttpRequest, q: str, collection: str | None = None) -> SuggestOut:
    client = solr_client()
    params: dict[str, object] = {
        "q": q,
        "rows": 8,
        "fl": "title_txt",
        "hl": False,
        "qf": "title_txt^4 text_txt",
    }
    if collection:
        params["fq"] = [f'collection_s:"{collection}"']
    body = client.select(solr_documents_collection(), **params)
    titles = [
        d.get("title_txt") for d in body.get("response", {}).get("docs", []) if d.get("title_txt")
    ]
    return SuggestOut(suggestions=list(dict.fromkeys(titles)))


@api.get("/stats", response=StatsOut, tags=["admin"])
def stats(request: HttpRequest) -> StatsOut:
    by_status = {
        row["status"]: row["n"] for row in Document.objects.values("status").annotate(n=Count("id"))
    }
    try:
        solr_count: int | None = solr_client().count(solr_documents_collection())
    except Exception:  # noqa: BLE001
        solr_count = None
    return StatsOut(
        collections=Collection.objects.count(),
        sources=Source.objects.count(),
        documents=by_status,
        solr_documents=solr_count,
    )
