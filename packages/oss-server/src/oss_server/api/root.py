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
from oss_server.api import accounts, curation, graph, knowledge, reconcile, searchplus, ui
from oss_server.api import search as search_mod
from oss_server.api.access import check, read_scope
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
api.add_router("/curation", curation.router)
api.add_router("/search", searchplus.router)
api.add_router("/ui", ui.router)


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
    qs = Collection.objects.annotate(document_count=Count("documents"))
    allowed = read_scope(request)
    if allowed is not None:
        qs = qs.filter(slug__in=allowed)
    return [_collection_out(c) for c in qs]


@api.post("/collections", response={201: CollectionOut}, tags=["collections"])
def create_collection(request: HttpRequest, payload: CollectionIn) -> tuple[int, CollectionOut]:
    check(request, "editor")
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
    check(request, "reader", slug)
    return _collection_out(
        get_object_or_404(Collection.objects.annotate(document_count=Count("documents")), slug=slug)
    )


def _stage_list(stages: str | None) -> list[str]:
    from oss_pipeline.registry import PROFILES

    known = {name for names in PROFILES.values() for name in names}
    wanted = [s.strip() for s in (stages or "").split(",") if s.strip()]
    unknown = [s for s in wanted if s not in known]
    if unknown:
        raise HttpError(400, f"unknown stages: {', '.join(unknown)}")
    return wanted


@api.post("/collections/{slug}/reindex", response={202: dict[str, int]}, tags=["collections"])
def reindex_collection_view(
    request: HttpRequest, slug: str, stages: str | None = None
) -> tuple[int, dict[str, int]]:
    """Replay enrichment and indexing for every indexed document from stored text.

    `stages=embed` (a comma list) replays only those stages, in profile order.
    """

    from oss_server.pipeline.tasks import reindex_collection

    get_object_or_404(Collection, slug=slug)
    check(request, "editor", slug)
    reindex_collection.delay(slug, _stage_list(stages) or None)
    return 202, {"queued": 1}


@api.delete("/collections/{slug}", response={204: None}, tags=["collections"])
def delete_collection(request: HttpRequest, slug: str) -> tuple[int, None]:
    check(request, "admin", slug)
    from oss_server.services import solr_chunks_collection

    c = get_object_or_404(Collection, slug=slug)
    term = search_mod._escape(slug)
    solr_client().delete_by_query(solr_documents_collection(), f"collection_s:{term}")
    solr_client().delete_by_query(solr_chunks_collection(), f"collection_s:{term}")
    c.delete()
    return 204, None


# --- sources ----------------------------------------------------------------------------------


SECRET_SETTING_WORDS = ("secret", "password", "token", "api_key", "apikey", "credential")


def redact_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Source settings with credential values masked: they are written, never read back."""

    out: dict[str, Any] = {}
    for key, value in settings.items():
        if any(word in key.lower() for word in SECRET_SETTING_WORDS) and value:
            out[key] = "********"
        elif isinstance(value, dict):
            out[key] = redact_settings(value)
        else:
            out[key] = value
    return out


def _source_out(s: Source) -> SourceOut:
    return SourceOut(
        id=s.id,
        collection=s.collection.slug,
        name=s.name,
        connector=s.connector,
        settings=redact_settings(s.settings),
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
    check(request, "reader", slug)
    return [
        _source_out(s)
        for s in c.sources.select_related("collection").annotate(document_count=Count("documents"))
    ]


@api.post("/collections/{slug}/sources", response={201: SourceOut}, tags=["sources"])
def create_source(request: HttpRequest, slug: str, payload: SourceIn) -> tuple[int, SourceOut]:
    from oss_pipeline.registry import connectors

    c = get_object_or_404(Collection, slug=slug)
    check(request, "editor", slug)
    if payload.connector not in connectors():
        raise HttpError(
            422, f"unknown connector {payload.connector}; known: {sorted(connectors())}"
        )
    from oss_pipeline.connectors.guard import SourceNotAllowed, check_source

    try:
        check_source(payload.connector, payload.settings, slug)
    except SourceNotAllowed as exc:
        raise HttpError(422, str(exc)) from exc
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
    check(request, "editor", s.collection.slug)
    result = discover_source.delay(s.id)
    return 202, RunOut(source_id=s.id, task_id=str(result.id))


@api.delete("/sources/{source_id}", response={204: None}, tags=["sources"])
def delete_source(request: HttpRequest, source_id: int) -> tuple[int, None]:
    s = get_object_or_404(Source, pk=source_id)
    check(request, "editor", s.collection.slug)
    from oss_server.services import solr_chunks_collection

    solr_client().delete_by_query(
        solr_documents_collection(), f"source_id_s:{search_mod._escape(str(s.id))}"
    )
    ids = list(s.documents.values_list("id", flat=True))
    for i in range(0, len(ids), 500):
        terms = "{!terms f=document_id}" + ",".join(ids[i : i + 500])
        solr_client().delete_by_query(solr_chunks_collection(), terms)
    s.delete()
    return 204, None


@api.post("/collections/{slug}/upload", response={202: list[DocumentOut]}, tags=["sources"])
def upload(
    request: HttpRequest, slug: str, files: File[list[UploadedFile]]
) -> tuple[int, list[DocumentOut]]:
    """Upload one or more files into the collection's `uploads` source and queue them."""

    from oss_core.config import get_settings
    from oss_pipeline.connectors.guard import SourceNotAllowed
    from oss_pipeline.connectors.upload import store_upload, upload_root
    from oss_server.catalogue.models import document_id
    from oss_server.pipeline.tasks import process_document

    c = get_object_or_404(Collection, slug=slug)
    check(request, "editor", slug)
    data_dir = get_settings().semascope_data_dir
    try:
        root = upload_root(data_dir, slug)
    except SourceNotAllowed as exc:  # a slug saved before slugs were checked
        raise HttpError(400, f"uploads refused: {exc}") from exc
    source, _ = Source.objects.get_or_create(
        collection=c,
        name="uploads",
        defaults={"connector": "upload", "settings": {"path": str(root)}},
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
    d = get_object_or_404(Document.objects.select_related("collection"), pk=doc_id)
    check(request, "reader", d.collection.slug)
    return _document_out(d)


@api.get("/documents/{doc_id}/text", response=DocumentTextOut, tags=["documents"])
def get_document_text(request: HttpRequest, doc_id: str) -> DocumentTextOut:
    d = get_object_or_404(Document.objects.select_related("collection"), pk=doc_id)
    check(request, "reader", d.collection.slug)
    return DocumentTextOut(id=d.id, text=d.text, pages=d.pages)


@api.get("/documents/{doc_id}/preview/{page}", tags=["documents"], url_name="preview")
def get_preview(request: HttpRequest, doc_id: str, page: int) -> FileResponse:
    """A rendered page (PDF) or thumbnail (image) as WebP."""

    d = get_object_or_404(Document.objects.select_related("collection"), pk=doc_id)
    check(request, "reader", d.collection.slug)
    preview_dir = d.fields.get("preview_dir")
    if not preview_dir or page < 1:
        raise HttpError(404, "no preview")
    file = Path(str(preview_dir)) / f"{page}.webp"
    if not file.is_file():
        raise HttpError(404, "no preview for that page")
    return FileResponse(file.open("rb"), content_type="image/webp")


@api.post("/documents/{doc_id}/reindex", response={202: dict[str, str]}, tags=["documents"])
def reindex_document(
    request: HttpRequest, doc_id: str, stages: str | None = None
) -> tuple[int, dict[str, str]]:
    from oss_server.pipeline.tasks import reindex_document as task

    d = get_object_or_404(Document.objects.select_related("collection"), pk=doc_id)
    check(request, "editor", d.collection.slug)
    result = task.delay(d.id, _stage_list(stages) or None)
    return 202, {"task_id": str(result.id)}


# --- search -----------------------------------------------------------------------------------


@api.post("/search", response=SearchOut, tags=["search"])
def search(request: HttpRequest, payload: SearchIn) -> SearchOut:
    allowed = read_scope(request, payload.collection)
    return search_mod.search(solr_client(), solr_documents_collection(), payload, allowed)


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


_SOLR_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')


def suggest_query(q: str) -> str:
    """Solr syntax escaped, every word required, the last one as a prefix: `odys` finds
    "The Odyssey" while it is being typed."""

    words = ["".join("\\" + ch if ch in _SOLR_SPECIAL else ch for ch in w) for w in q.split()]
    words = [w for w in words if w]
    if not words:
        return ""
    words[-1] += "*"
    return " AND ".join(words)


@api.get("/suggest", response=SuggestOut, tags=["search"])
def suggest(request: HttpRequest, q: str, collection: str | None = None) -> SuggestOut:
    """Up to eight document titles matching what is being typed."""

    query = suggest_query(q)
    if not query:
        return SuggestOut(suggestions=[])
    if collection:
        check(request, "reader", collection)
    params: dict[str, object] = {
        "q": query,
        "defType": "edismax",
        "qf": "title_txt",
        "rows": 8,
        "fl": "title_txt",
        "hl": False,
    }
    allowed = read_scope(request, collection)
    fq = search_mod.collection_filter(allowed)
    if collection:
        fq.append(f"collection_s:{search_mod._escape(collection)}")
    if fq:
        params["fq"] = fq
    body = solr_client().select(solr_documents_collection(), **params)
    titles = [
        str(d["title_txt"][0] if isinstance(d.get("title_txt"), list) else d.get("title_txt"))
        for d in body.get("response", {}).get("docs", [])
        if d.get("title_txt")
    ]
    return SuggestOut(suggestions=list(dict.fromkeys(titles)))


@api.get("/stats", response=StatsOut, tags=["admin"])
def stats(request: HttpRequest) -> StatsOut:
    allowed = read_scope(request)
    collections = Collection.objects.all()
    sources = Source.objects.all()
    documents = Document.objects.all()
    if allowed is not None:
        collections = collections.filter(slug__in=allowed)
        sources = sources.filter(collection__slug__in=allowed)
        documents = documents.filter(collection__slug__in=allowed)
    by_status = {
        row["status"]: row["n"] for row in documents.values("status").annotate(n=Count("id"))
    }
    try:
        body = solr_client().select(
            solr_documents_collection(),
            q="*:*",
            fq=search_mod.collection_filter(allowed),
            rows=0,
            hl=False,
        )
        solr_count: int | None = int(body["response"]["numFound"])
    except Exception:  # noqa: BLE001
        solr_count = None
    return StatsOut(
        collections=collections.count(),
        sources=sources.count(),
        documents=by_status,
        solr_documents=solr_count,
        chunk_models=chunk_models(allowed),
        embed_model=search_mod.current_embed_model(),
    )


def chunk_models(allowed: list[str] | None = None) -> dict[str, int]:
    """How many chunk vectors each embedding model produced; a reindex is due when they differ."""

    from oss_server.services import solr_chunks_collection

    try:
        body = solr_client().select(
            solr_chunks_collection(),
            q="*:*",
            fq=search_mod.collection_filter(allowed),
            rows=0,
            facet=True,
            **{"facet.field": "model_s", "facet.mincount": 1},
        )
    except Exception:  # noqa: BLE001
        return {}
    flat = body.get("facet_counts", {}).get("facet_fields", {}).get("model_s", [])
    return {str(flat[i]): int(flat[i + 1]) for i in range(0, len(flat), 2)}
