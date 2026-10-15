"""Discovery and processing tasks.

discover_source walks a source with its connector, upserts Document rows and
fans out process_document for new or changed items. process_document runs the
collection's profile and stores the result. Both are idempotent.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from oss_core.config import get_settings
from oss_core.contracts import Cursor, SourceConfig, SourceItem, StageContext, WorkingDocument
from oss_pipeline.registry import connectors, profile
from oss_pipeline.stages.base import run_profile
from oss_server.catalogue.models import Collection, Document, Source, document_id
from oss_server.services import solr_client, solr_documents_collection

log = logging.getLogger(__name__)


def stage_settings(collection: Collection) -> dict[str, Any]:
    s = get_settings()
    base: dict[str, Any] = {
        "blob_dir": str(s.semascope_data_dir / "blobs"),
        "tika_url": s.tika_url,
        "solr_url": s.solr_url,
        "solr_user": s.solr_user,
        "solr_password": s.solr_password.get_secret_value() if s.solr_password else None,
        "solr_documents": solr_documents_collection(),
        "ocr": True,
    }
    base.update(collection.settings.get("pipeline", {}))
    return base


@shared_task(name="semascope.discover_source", queue="io")
def discover_source(source_id: int, *, full: bool = True) -> dict[str, int]:
    source = Source.objects.select_related("collection").get(pk=source_id)
    connector = connectors().get(source.connector)
    if connector is None:
        Source.objects.filter(pk=source_id).update(
            status=Source.Status.FAILED, last_error=f"unknown connector {source.connector}"
        )
        return {"error": 1}
    Source.objects.filter(pk=source_id).update(status=Source.Status.RUNNING, last_error="")
    config = SourceConfig(
        id=str(source.id),
        collection=source.collection.slug,
        connector=source.connector,
        settings=source.settings,
    )
    since = None if full or not source.cursor else Cursor.model_validate(source.cursor)
    started = timezone.now()
    counts = {"seen": 0, "queued": 0, "unchanged": 0, "deleted": 0}
    try:
        for item in connector.discover(config, since):
            counts["seen"] += 1
            doc_id = document_id(source.collection.slug, item.uri)
            with transaction.atomic():
                doc, created = Document.objects.select_for_update().get_or_create(
                    id=doc_id,
                    defaults={
                        "collection": source.collection,
                        "source": source,
                        "uri": item.uri,
                        "content_hash": item.content_hash or "",
                        "size": item.size,
                        "modified_at": item.modified_at,
                        "status": Document.Status.QUEUED,
                    },
                )
                changed = (
                    created
                    or doc.content_hash != (item.content_hash or "")
                    or doc.status in (Document.Status.FAILED, Document.Status.QUEUED)
                )
                doc.seen_at = started
                if changed:
                    doc.content_hash = item.content_hash or ""
                    doc.size = item.size
                    doc.modified_at = item.modified_at
                    doc.status = Document.Status.QUEUED
                    doc.error = ""
                doc.save()
            if changed:
                counts["queued"] += 1
                process_document.delay(doc_id, item.model_dump(mode="json"))
            else:
                counts["unchanged"] += 1
        if full:
            gone = (
                Document.objects.filter(source=source)
                .exclude(seen_at=started)
                .exclude(status=Document.Status.DELETED)
            )
            for doc in gone:
                delete_document.delay(doc.id)
                counts["deleted"] += 1
        Source.objects.filter(pk=source_id).update(
            status=Source.Status.IDLE,
            last_run_at=started,
            cursor=Cursor(value="full", issued_at=started).model_dump(mode="json"),
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("discover_source %s failed", source_id)
        Source.objects.filter(pk=source_id).update(
            status=Source.Status.FAILED, last_error=f"{type(exc).__name__}: {exc}"[:2000]
        )
        raise
    return counts


@shared_task(name="semascope.process_document", queue="cpu", acks_late=True)
def process_document(doc_id: str, item: dict[str, Any]) -> str:
    doc_row = Document.objects.select_related("collection", "source").get(pk=doc_id)
    Document.objects.filter(pk=doc_id).update(status=Document.Status.PROCESSING)
    connector = connectors()[doc_row.source.connector]
    fetched = connector.fetch(SourceItem.model_validate(item))
    work = WorkingDocument(
        id=doc_row.id,
        collection=doc_row.collection.slug,
        source_id=str(doc_row.source_id),
        uri=doc_row.uri,
        content_hash=doc_row.content_hash,
        media_type=fetched.media_type,
        metadata={
            "modified_at": doc_row.modified_at.isoformat() if doc_row.modified_at else None,
            "size": doc_row.size,
        },
        fields={"local_path": fetched.path} if fetched.path else {},
    )
    ctx = StageContext(
        collection=work.collection,
        profile=doc_row.collection.profile,
        settings=stage_settings(doc_row.collection),
    )
    work = run_profile(profile(ctx.profile), work, ctx)
    _store(doc_row, work)
    return doc_row.status


@shared_task(name="semascope.reindex_document", queue="cpu")
def reindex_document(doc_id: str) -> str:
    """Replay enrichment and indexing from the stored extraction; no Tika."""

    doc_row = Document.objects.select_related("collection", "source").get(pk=doc_id)
    work = WorkingDocument(
        id=doc_row.id,
        collection=doc_row.collection.slug,
        source_id=str(doc_row.source_id),
        uri=doc_row.uri,
        content_hash=doc_row.content_hash,
        media_type=doc_row.media_type or None,
        language=doc_row.language or None,
        title=doc_row.title or None,
        text=doc_row.text,
        pages=doc_row.pages,
        metadata=dict(doc_row.metadata),
        fields={k: v for k, v in doc_row.fields.items() if k != "indexed"},
    )
    ctx = StageContext(
        collection=work.collection,
        profile=doc_row.collection.profile,
        settings=stage_settings(doc_row.collection),
    )
    stages = [s for s in profile(ctx.profile) if s.name not in ("fetch", "extract")]
    work = run_profile(stages, work, ctx)
    _store(doc_row, work)
    return doc_row.status


@shared_task(name="semascope.delete_document", queue="io")
def delete_document(doc_id: str) -> None:
    solr_client().delete_by_id(solr_documents_collection(), [doc_id])
    Document.objects.filter(pk=doc_id).update(
        status=Document.Status.DELETED, text="", pages=[], indexed_at=None
    )


def _store(row: Document, work: WorkingDocument) -> None:
    failed = [r for r in work.stage_runs if r.outcome == "failed"]
    indexed = bool(work.fields.get("indexed"))
    row.media_type = work.media_type or ""
    row.language = work.language or ""
    row.title = work.title or ""
    row.text = work.text or ""
    row.pages = work.pages
    row.metadata = _jsonable(work.metadata)
    row.fields = _jsonable({k: v for k, v in work.fields.items() if k not in ("local_path",)})
    row.tags = [t.model_dump(mode="json") for t in work.tags]
    row.stage_runs = [r.model_dump(mode="json") for r in work.stage_runs]
    row.blob_path = str(work.fields.get("blob_path") or "")
    row.status = Document.Status.INDEXED if indexed else Document.Status.FAILED
    row.error = "; ".join(f"{r.stage}: {r.message}" for r in failed)[:2000]
    row.indexed_at = datetime.now(UTC) if indexed else None
    row.save()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
