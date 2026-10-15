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


def pack_versions_for(collection: Collection) -> dict[str, str]:
    """Active pack versions this collection tags with: its bindings, or every active pack."""

    from oss_server.knowledge.models import Pack, PackBinding

    active = {p.pack_id: p.version for p in Pack.objects.filter(status=Pack.Status.ACTIVE)}
    bound = list(
        PackBinding.objects.filter(collection=collection, enabled=True).order_by("precedence")
    )
    if not bound:
        return active
    return {b.pack_id: active[b.pack_id] for b in bound if b.pack_id in active}


def build_context(collection: Collection) -> StageContext:
    """The StageContext a collection's stages run with: settings plus active pack versions."""

    return StageContext(
        collection=collection.slug,
        profile=collection.profile,
        settings=stage_settings(collection),
        pack_versions=pack_versions_for(collection),
    )


def stage_settings(collection: Collection) -> dict[str, Any]:
    s = get_settings()
    base: dict[str, Any] = {
        "blob_dir": str(s.semascope_data_dir / "blobs"),
        "tika_url": s.tika_url,
        "solr_url": s.solr_url,
        "solr_user": s.solr_user,
        "solr_password": s.solr_password.get_secret_value() if s.solr_password else None,
        "solr_documents": solr_documents_collection(),
        "solr_entities": "entities",
        "neo4j_uri": s.neo4j_uri,
        "neo4j_user": s.neo4j_user,
        "neo4j_password": s.neo4j_password.get_secret_value() if s.neo4j_password else "",
        "pseudonymise": bool(collection.settings.get("pseudonymise", False)),
        "pseudonymise_secret": s.semascope_secret_key.get_secret_value(),
        "ocr": True,
    }
    base["shapes_ttl"] = active_shapes()
    base.update(collection.settings.get("pipeline", {}))
    return base


def active_shapes() -> str:
    """Every active pack's shapes.ttl, concatenated; rdflib tolerates repeated prefixes."""

    from pathlib import Path

    from oss_server.knowledge.models import Pack

    parts: list[str] = []
    for pack in Pack.objects.filter(status=Pack.Status.ACTIVE):
        file = Path(pack.source_path) / "shapes.ttl"
        if file.exists():
            parts.append(file.read_text(encoding="utf-8"))
    return "\n".join(parts)


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
    meta = fetched.item.metadata
    fields: dict[str, Any] = {"local_path": fetched.path} if fetched.path else {}
    fields.update({k: v for k, v in (meta.get("fields") or {}).items() if str(k).startswith("x_")})
    if meta.get("parent_uri"):
        fields["x_parent_uri_s"] = str(meta["parent_uri"])
    for keep in ("curated", "dismissed_violations"):
        if doc_row.fields.get(keep):
            fields[keep] = doc_row.fields[keep]
    work = WorkingDocument(
        id=doc_row.id,
        collection=doc_row.collection.slug,
        source_id=str(doc_row.source_id),
        uri=doc_row.uri,
        content_hash=doc_row.content_hash,
        media_type=fetched.media_type,
        title=str(meta["title"]) if meta.get("title") else None,
        metadata={
            "modified_at": doc_row.modified_at.isoformat() if doc_row.modified_at else None,
            "size": doc_row.size,
        },
        fields=fields,
    )
    ctx = build_context(doc_row.collection)
    work = run_profile(profile(ctx.profile), work, ctx)
    _store(doc_row, work)
    return doc_row.status


@shared_task(name="semascope.reindex_document", queue="cpu")
def reindex_document(doc_id: str) -> str:
    """Replay enrichment and indexing from the stored extraction; no Tika."""

    doc_row = Document.objects.select_related("collection", "source").get(pk=doc_id)
    Document.objects.filter(pk=doc_id).update(status=Document.Status.PROCESSING)
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
    ctx = build_context(doc_row.collection)
    stages = [s for s in profile(ctx.profile) if s.name not in ("fetch", "extract")]
    work = run_profile(stages, work, ctx)
    _store(doc_row, work)
    return doc_row.status


@shared_task(name="semascope.reindex_collection", queue="io")
def reindex_collection(slug: str) -> int:
    """Queue a reindex of every indexed document in a collection (after a pack change)."""

    ids = list(
        Document.objects.filter(collection__slug=slug, status=Document.Status.INDEXED).values_list(
            "id", flat=True
        )
    )
    for doc_id in ids:
        reindex_document.delay(doc_id)
    return len(ids)


@shared_task(name="semascope.tick", queue="io")
def tick() -> dict[str, int]:
    """Once a minute: start every enabled, scheduled source that is due."""

    from oss_server.pipeline.schedule import is_due

    now = timezone.now()
    started = 0
    for source in (
        Source.objects.filter(enabled=True)
        .exclude(schedule="")
        .exclude(status=Source.Status.RUNNING)
    ):
        if is_due(source.schedule, source.last_run_at, now):
            discover_source.delay(source.id)
            started += 1
    deliver_webhooks.delay()
    check_alerts.delay()
    return {"started": started}


@shared_task(name="semascope.delete_document", queue="io")
def delete_document(doc_id: str) -> None:
    solr_client().delete_by_id(solr_documents_collection(), [doc_id])
    try:
        from oss_server.services import graph_client

        graph_client().execute(
            "MATCH (d:Document {id: $id}) OPTIONAL MATCH (d)-[r]-() WHERE type(r) <> 'CURATED' "
            "DELETE r WITH d WHERE NOT (d)--() DELETE d",
            id=doc_id,
        )
    except Exception:  # noqa: BLE001 - the graph is a projection; Solr and Postgres are the record
        log.exception("graph delete failed for %s", doc_id)
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
    if indexed:
        try:
            from oss_server.curation.services import emit, queue_reviews

            opened = queue_reviews(row)
            emit(
                "document.indexed",
                collection=row.collection.slug,
                subject=row.id,
                payload={
                    "uri": row.uri,
                    "title": row.title,
                    "tags": len(row.tags),
                    "reviews_opened": opened,
                },
            )
        except Exception:  # noqa: BLE001 - never fail indexing on curation bookkeeping
            log.exception("review queue failed for %s", row.id)


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


@shared_task(name="semascope.deliver_webhooks", queue="io")
def deliver_webhooks() -> int:
    """Send outbox events to every enabled webhook, in order, with an HMAC signature."""

    import hashlib
    import hmac
    import json

    import httpx

    from oss_server.curation.models import Event, Webhook

    sent = 0
    for hook in Webhook.objects.filter(enabled=True):
        events = Event.objects.filter(pk__gt=hook.last_event_id).order_by("pk")[:100]
        for event in events:
            if hook.kinds and not any(event.kind.startswith(k) for k in hook.kinds):
                hook.last_event_id = event.pk
                continue
            body = json.dumps(
                {
                    "id": event.pk,
                    "kind": event.kind,
                    "collection": event.collection,
                    "subject": event.subject,
                    "payload": event.payload,
                    "created_at": event.created_at.isoformat(),
                },
                default=str,
            ).encode()
            headers = {"Content-Type": "application/json", "X-Semascope-Event": event.kind}
            if hook.secret:
                headers["X-Semascope-Signature"] = (
                    "sha256=" + hmac.new(hook.secret.encode(), body, hashlib.sha256).hexdigest()
                )
            try:
                resp = httpx.post(hook.url, content=body, headers=headers, timeout=15.0)
                if resp.status_code >= 400:
                    hook.last_error = f"{resp.status_code} on event {event.pk}"
                    break
                hook.last_event_id = event.pk
                hook.last_error = ""
                sent += 1
            except httpx.HTTPError as exc:
                hook.last_error = f"{type(exc).__name__} on event {event.pk}"
                break
        hook.save(update_fields=["last_event_id", "last_error"])
    return sent


@shared_task(name="semascope.check_alerts", queue="io")
def check_alerts() -> int:
    """Run every saved search with an alert; emit an event when its total grew."""

    from oss_server.api import search as search_mod
    from oss_server.api.schemas import SearchIn
    from oss_server.catalogue.models import SavedSearch
    from oss_server.curation.services import emit

    fired = 0
    for saved in SavedSearch.objects.filter(alert=True).select_related("user"):
        try:
            req = SearchIn(**saved.query).model_copy(
                update={"page": 1, "page_size": 5, "facets": []}
            )
            res = search_mod.search(solr_client(), solr_documents_collection(), req)
        except Exception:  # noqa: BLE001
            log.exception("alert %s failed", saved.pk)
            continue
        if saved.last_checked_at is not None and res.total > saved.last_total:
            emit(
                "alert.saved_search",
                collection=str(saved.query.get("collection") or ""),
                subject=saved.name,
                payload={
                    "user": saved.user.username,
                    "new": res.total - saved.last_total,
                    "total": res.total,
                    "hits": [h.model_dump() for h in res.hits],
                },
            )
            fired += 1
        saved.last_total = res.total
        saved.last_checked_at = timezone.now()
        saved.save(update_fields=["last_total", "last_checked_at"])
    return fired
