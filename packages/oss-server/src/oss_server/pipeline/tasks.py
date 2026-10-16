"""Discovery and processing tasks.

discover_source walks a source with its connector, upserts Document rows and
fans out process_document for new or changed items. process_document runs the
collection's profile and stores the result. Both are idempotent.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from oss_core.config import get_settings
from oss_core.contracts import (
    Cursor,
    SourceConfig,
    SourceItem,
    StageContext,
    Tag,
    WorkingDocument,
)
from oss_pipeline.registry import connectors, profile
from oss_pipeline.retry import backoff, is_transient
from oss_pipeline.stages.base import TRANSIENT_FAILURES, run_profile
from oss_server.catalogue.models import Collection, Document, Source, document_id
from oss_server.services import solr_client, solr_documents_collection

log = logging.getLogger(__name__)


def pack_versions_for(collection: Collection) -> dict[str, str]:
    """Active pack versions this collection tags with: its bindings, or every active pack.

    The dict is in precedence order (`knowledge.precedence.pack_order`); the tag
    and link stages resolve an IRI or label that two packs share by that order.
    """

    from oss_server.knowledge.models import Pack
    from oss_server.knowledge.precedence import pack_order

    active = {p.pack_id: p.version for p in Pack.objects.filter(status=Pack.Status.ACTIVE)}
    return {pid: active[pid] for pid in pack_order(collection) if pid in active}


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
        "solr_chunks": "chunks",
        "embedder": s.semascope_embedder,
        "embed_model": s.semascope_embed_model,
        "embed_base_url": s.semascope_embed_base_url,
        "embed_api_key": s.remote_key("embed"),
        "ner_provider": s.semascope_ner_provider,
        "ner_model": s.semascope_ner_model,
        "ner_base_url": s.semascope_ner_base_url,
        "ner_api_key": s.remote_key("ner"),
        "ner_labels": active_entity_labels(),
        "extract_provider": s.semascope_extract_provider,
        "extract_base_url": s.semascope_extract_base_url,
        "extract_api_key": s.remote_key("extract"),
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


def pseudonymise_secret(collection: Collection) -> str | None:
    """The HMAC secret the graph stage keys this collection's NER entities with, or None.

    Mirrors what the graph stage reads from `stage_settings` (a `pipeline`
    override included), so other writers, such as curation, key entities the same way.
    """

    pipeline = collection.settings.get("pipeline", {}) or {}
    if not pipeline.get("pseudonymise", collection.settings.get("pseudonymise", False)):
        return None
    secret = pipeline.get("pseudonymise_secret") or get_settings().semascope_secret_key
    return str(secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret)


BASE_PACK_ID = "semascope-base"  # its classes are the product's own model, not things to find


def active_entity_labels() -> dict[str, dict[str, str]]:
    """Zero-shot entity labels from the active packs' classes: label -> class and description.

    Only the spaCy provider ignores these; GLiNER (local or remote) asks for every class a
    pack declares, so a mythology pack finds deities and a property pack finds easements.
    The base pack is skipped: Document, Chunk, Source and the four base entity classes are
    the product's structure, and the base four are always asked for anyway.
    """

    from oss_server.knowledge.models import Concept, Pack

    out: dict[str, dict[str, str]] = {}
    packs = Pack.objects.filter(status=Pack.Status.ACTIVE).exclude(pack_id=BASE_PACK_ID)
    rows = Concept.objects.filter(pack__in=packs, kind=Concept.Kind.CLASS).values_list(
        "pack__requirements", "iri", "pref_label", "definition"
    )
    for requirements, iri, label, definition in rows:
        name = str(label or "").strip()
        if not name:
            continue
        local = re.split(r"[#/]", str(iri))[-1] or name
        # a pack may name the classes worth looking for (`semascope.entity_classes`); the
        # default is every class it declares
        chosen = ((requirements or {}).get("semascope") or {}).get("entity_classes")
        if chosen is not None and local not in chosen and name not in chosen:
            continue
        cls = "".join(part[:1].upper() + part[1:] for part in re.split(r"[\s_-]+", local) if part)
        out[name.lower()] = {"class": cls, "description": (definition or name).strip()[:300]}
    return out


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


TASK_RETRIES = 3  # re-runs of a document task after transient failures (settings: task_retries)
TASK_BACKOFF_S = 60.0  # first wait, doubling: 60 s, 120 s, 240 s (settings: task_retry_backoff_s)


def _retry_policy(collection: Collection) -> tuple[int, float]:
    pipeline = collection.settings.get("pipeline", {}) or {}
    return (
        max(0, int(pipeline.get("task_retries", TASK_RETRIES))),
        float(pipeline.get("task_retry_backoff_s", TASK_BACKOFF_S)),
    )


def _retry_if_transient(task: Any, row: Document, work: WorkingDocument) -> None:
    """After `_store`: re-queue the task when every failed stage failed transiently.

    The stored result stays (what is published is what Postgres says); a failed
    document is shown as queued again with the reason. Nothing is retried when any
    stage failed permanently, and never more than `task_retries` times.
    """

    transient = set(work.fields.get(TRANSIENT_FAILURES) or [])
    failed = {r.stage for r in work.stage_runs if r.outcome == "failed"}
    if not transient or not failed or not failed <= transient:
        return
    retries, base = _retry_policy(row.collection)
    attempt = int(getattr(task.request, "retries", 0) or 0) + 1
    if attempt > retries:
        return
    countdown = backoff(attempt, base)
    note = f"; retry {attempt}/{retries} in {countdown:.0f}s ({', '.join(sorted(failed))})"
    Document.objects.filter(pk=row.pk).update(
        status=Document.Status.QUEUED if row.status == Document.Status.FAILED else row.status,
        error=(row.error + note)[:2000],
    )
    raise task.retry(countdown=countdown, max_retries=retries)


@shared_task(bind=True, name="semascope.process_document", queue="cpu", acks_late=True)
def process_document(self: Any, doc_id: str, item: dict[str, Any]) -> str:
    doc_row = Document.objects.select_related("collection", "source").get(pk=doc_id)
    Document.objects.filter(pk=doc_id).update(status=Document.Status.PROCESSING)
    connector = connectors()[doc_row.source.connector]
    try:
        fetched = connector.fetch(SourceItem.model_validate(item))
    except Exception as exc:  # noqa: BLE001 - recorded on the document
        retries, base = _retry_policy(doc_row.collection)
        attempt = int(getattr(self.request, "retries", 0) or 0) + 1
        transient = is_transient(exc)
        error = f"fetch: {type(exc).__name__}: {exc}"[:1900]
        if transient and attempt <= retries:
            countdown = backoff(attempt, base)
            Document.objects.filter(pk=doc_id).update(
                status=Document.Status.QUEUED,
                error=f"{error}; retry {attempt}/{retries} in {countdown:.0f}s",
            )
            raise self.retry(exc=exc, countdown=countdown, max_retries=retries) from exc
        Document.objects.filter(pk=doc_id).update(status=Document.Status.FAILED, error=error)
        return str(Document.Status.FAILED)
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
    _retry_if_transient(self, doc_row, work)
    return doc_row.status


PREREQS = {"embed": ("structure",)}  # not stored on the row, so recomputed for the replayed stage
FOLLOWERS = {"tag": ("link",), "ner": ("link",)}  # stages whose output the replayed one feeds
PUBLISH = ("index", "graph")  # always rerun after a partial replay: they publish the result


def replay_set(stages: list[str] | None) -> set[str]:
    """The stages a partial replay runs: the requested ones, what they need, what they feed, and
    the publishers."""

    wanted = {s for s in (stages or []) if s}
    if not wanted:
        return set()
    for name in list(wanted):
        wanted.update(PREREQS.get(name, ()))
        wanted.update(FOLLOWERS.get(name, ()))
    wanted.update(PUBLISH)
    return wanted


@shared_task(bind=True, name="semascope.reindex_document", queue="cpu")
def reindex_document(self: Any, doc_id: str, stages: list[str] | None = None) -> str:
    """Replay enrichment and indexing from the stored extraction; no Tika.

    `stages` limits the replay to those stages of the profile (plus the stages they feed and
    the index and graph stages that publish the result), in profile order: after a change of
    embedding model only `["embed"]` has to be recomputed.
    """

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
    wanted = replay_set(stages)
    if wanted:
        # a partial replay keeps the tags of the stages it does not run and recomputes the rest
        work.tags = [Tag(**t) for t in doc_row.tags if t.get("stage") not in wanted]
    todo = [
        s
        for s in profile(ctx.profile)
        if s.name not in ("fetch", "extract") and (not wanted or s.name in wanted)
    ]
    # a partial replay publishes nothing when a replayed stage fails: the published result
    # stays as it was and the document reports the failure
    work = run_profile(todo, work, ctx, stop_on_failure=tuple(wanted - set(PUBLISH)))
    _store(doc_row, work)
    _retry_if_transient(self, doc_row, work)
    return doc_row.status


@shared_task(name="semascope.reindex_collection", queue="io")
def reindex_collection(slug: str, stages: list[str] | None = None) -> int:
    """Queue a reindex of every indexed document in a collection (after a pack change)."""

    # indexed documents, and failed ones whose extraction succeeded (a replay may fix them)
    ids = list(
        Document.objects.filter(
            collection__slug=slug,
            status__in=(Document.Status.INDEXED, Document.Status.FAILED),
        )
        .exclude(text="")
        .values_list("id", flat=True)
    )
    for doc_id in ids:
        reindex_document.delay(doc_id, stages or None)
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


@shared_task(bind=True, name="semascope.delete_document", queue="io")
def delete_document(self: Any, doc_id: str) -> None:
    try:
        solr_client().delete_by_id(solr_documents_collection(), [doc_id])
    except Exception as exc:
        if is_transient(exc) and self.request.retries < TASK_RETRIES:
            raise self.retry(
                exc=exc, countdown=backoff(self.request.retries + 1, TASK_BACKOFF_S)
            ) from exc
        raise
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
    _record_metrics(row, work, indexed)
    row.media_type = work.media_type or ""
    row.language = work.language or ""
    row.title = work.title or ""
    row.text = work.text or ""
    row.pages = work.pages
    row.metadata = _jsonable(work.metadata)
    row.fields = _jsonable(
        {k: v for k, v in work.fields.items() if k not in ("local_path", TRANSIENT_FAILURES)}
    )
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


@lru_cache(maxsize=1)
def _instruments() -> tuple[Any, Any]:
    """OpenTelemetry instruments; no-ops until the worker sets a meter provider."""

    from opentelemetry import metrics

    meter = metrics.get_meter("semascope.pipeline")
    return (
        meter.create_counter(
            "semascope.documents", unit="{document}", description="Documents processed, by status"
        ),
        meter.create_histogram(
            "semascope.stage.duration", unit="ms", description="Stage run time, by outcome"
        ),
    )


def _record_metrics(row: Document, work: WorkingDocument, indexed: bool) -> None:
    try:
        documents, stage_duration = _instruments()
        collection = row.collection.slug
        documents.add(1, {"collection": collection, "status": "indexed" if indexed else "failed"})
        for r in work.stage_runs:
            stage_duration.record(r.duration_ms, {"stage": r.stage, "outcome": r.outcome})
    except Exception:  # noqa: BLE001 - metrics never fail indexing
        log.debug("metrics not recorded", exc_info=True)


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
