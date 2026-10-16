"""Discovery and processing tasks.

discover_source walks a source with its connector, upserts Document rows and
fans out process_document for new or changed items. process_document runs the
collection's profile and stores the result. Both are idempotent: delivery is at
least once, so a task may run again after its worker died, and running it again
gives the same result (and costs nothing when the document is already indexed
with the same inputs). `recover` requeues documents whose worker died and
re-projects documents whose graph write waited for Neo4j; see
docs/operations.md#crashes-and-recovery.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from celery import shared_task
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from oss_core.config import MAX_RETRY_COUNTDOWN_S, get_settings
from oss_core.contracts import (
    Cursor,
    SourceConfig,
    SourceItem,
    Stage,
    StageContext,
    StageRun,
    Tag,
    WorkingDocument,
)
from oss_pipeline.clients.solr import quote_term
from oss_pipeline.registry import connectors, profile
from oss_pipeline.retry import backoff, is_transient
from oss_pipeline.stages.base import TRANSIENT_FAILURES, run_profile, run_stage
from oss_pipeline.stages.index import custom_fields
from oss_server.catalogue.models import Collection, Document, Source, document_id
from oss_server.pipeline.overrides import pipeline_overrides
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
    packs = list(pack_versions_for(collection))  # the collection's bindings, or every active pack
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
        "ner_labels": active_entity_labels(packs),
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
    base["shapes_ttl"] = active_shapes(packs)
    base.update(pipeline_overrides(collection.settings))  # tuning only, never addresses
    return base


def pseudonymise_secret(collection: Collection) -> str | None:
    """The HMAC secret the graph stage keys this collection's NER entities with, or None.

    Mirrors what the graph stage reads from `stage_settings` (a `pipeline`
    override included), so other writers, such as curation, key entities the same way.
    """

    pipeline = pipeline_overrides(collection.settings)
    if not pipeline.get("pseudonymise", collection.settings.get("pseudonymise", False)):
        return None
    return get_settings().semascope_secret_key.get_secret_value()


BASE_PACK_ID = "semascope-base"  # its classes are the product's own model, not things to find


def active_entity_labels(pack_ids: list[str] | None = None) -> dict[str, dict[str, str]]:
    """Zero-shot entity labels from the active packs' classes: label -> class and description.

    `pack_ids` limits them to those packs (a collection's bindings); None means every active
    pack (the search page's facets, which span collections).

    Only the spaCy provider ignores these; GLiNER (local or remote) asks for every class a
    pack declares, so a mythology pack finds deities and a property pack finds easements.
    The base pack is skipped: Document, Chunk, Source and the four base entity classes are
    the product's structure, and the base four are always asked for anyway.
    """

    from oss_server.knowledge.models import Concept, Pack

    out: dict[str, dict[str, str]] = {}
    packs = Pack.objects.filter(status=Pack.Status.ACTIVE).exclude(pack_id=BASE_PACK_ID)
    if pack_ids is not None:
        packs = packs.filter(pack_id__in=pack_ids)
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


def active_shapes(pack_ids: list[str] | None = None) -> str:
    """The active packs' shapes.ttl (only `pack_ids`, a collection's bindings, when given),
    concatenated; rdflib tolerates repeated prefixes."""

    from pathlib import Path

    from oss_server.knowledge.models import Pack

    parts: list[str] = []
    active = Pack.objects.filter(status=Pack.Status.ACTIVE)
    if pack_ids is not None:
        active = active.filter(pack_id__in=pack_ids)
    for pack in active:
        file = Path(pack.source_path) / "shapes.ttl"
        if file.exists():
            parts.append(file.read_text(encoding="utf-8"))
    return "\n".join(parts)


QUEUED_TTL_S = 6 * 3600  # a document queued this recently has its task waiting: not queued again
MIN_YIELD = 0.5  # a run that sees less than this share of a source's documents deletes nothing
MIN_YIELD_FLOOR = 10  # ... once the source has at least this many
HOLD_CHECK_EVERY = 500  # items between checks that the run still holds its source


class RunLost(Exception):  # noqa: N818
    """`recover` released the source while this run was slow; a newer run may hold it."""


def _known(source: Source) -> Any:
    """What the last runs recorded per URI, for connectors that skip re-reading unchanged files."""

    rows = Document.objects.filter(source=source).values_list(
        "uri", "size", "modified_at", "content_hash"
    )
    known = {uri: (size, modified, digest) for uri, size, modified, digest in rows}
    return known.get


@shared_task(name="semascope.discover_source", queue="io")
def discover_source(source_id: int, *, full: bool = True) -> dict[str, int]:
    """One run of a source: queue what is new or changed, then delete what is gone.

    One run per source at a time (`runs.claim_run`): a second run finds the source `running`
    and returns at once. A document deleted earlier comes back when the source yields it
    again. What a full run did not see is deleted only when the connector deletes by absence
    (feeds do not), the run could read everything it tried (`RunReport`), it did not stop
    at a limit, and it saw at least `min_yield` (0.5) of the documents the source had; else
    nothing is deleted and the source says why (docs/adr/0013-deletion-and-retention.md).
    """

    from oss_pipeline.connectors.base import remove_work_file
    from oss_pipeline.connectors.guard import SourceNotAllowed, check_source
    from oss_server.pipeline.runs import claim_run, finish_run, holds

    source = Source.objects.select_related("collection").get(pk=source_id)
    now = timezone.now()
    connector = connectors().get(source.connector)
    if connector is None:
        Source.objects.filter(pk=source_id).update(
            status=Source.Status.FAILED,
            last_error=f"unknown connector {source.connector}",
            last_run_at=now,
        )
        return {"error": 1}
    try:
        check_source(source.connector, source.settings, source.collection.slug)
    except SourceNotAllowed as exc:
        Source.objects.filter(pk=source_id).update(
            status=Source.Status.FAILED, last_error=f"refused: {exc}"[:2000], last_run_at=now
        )
        return {"error": 1}
    started = timezone.now()
    run_id = claim_run(source_id, started)
    if run_id is None:
        log.info("discover_source %s: a run of this source is in progress; not started", source_id)
        return {"busy": 1}
    config = SourceConfig(
        id=str(source.id),
        collection=source.collection.slug,
        connector=source.connector,
        settings=source.settings,
    )
    since = None if full or not source.cursor else Cursor.model_validate(source.cursor)
    held_after = max(1, get_settings().semascope_max_attempts)
    queued_since = started - timedelta(seconds=QUEUED_TTL_S)
    live_before = Document.objects.filter(source=source).exclude(status=Document.Status.DELETED)
    counts = {"seen": 0, "queued": 0, "unchanged": 0, "deleted": 0, "before": live_before.count()}
    kwargs: dict[str, Any] = {}
    if "known" in inspect.signature(connector.discover).parameters:
        kwargs["known"] = _known(source)
    try:
        for item in connector.discover(config, since, **kwargs):
            counts["seen"] += 1
            if counts["seen"] % HOLD_CHECK_EVERY == 0 and not holds(source_id, run_id):
                raise RunLost
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
                new_content = doc.content_hash != (item.content_hash or "")
                # a document deleted by an earlier run is back; one whose runs kept killing
                # the worker is held until it changes or is reindexed; one queued a moment ago
                # has its task waiting; one left `processing` by a dead worker is retried
                changed = (
                    created
                    or new_content
                    or doc.status == Document.Status.DELETED
                    or (doc.status == Document.Status.FAILED and doc.attempts < held_after)
                    or (
                        doc.status == Document.Status.QUEUED
                        and doc.attempts < held_after
                        and doc.updated_at < queued_since
                    )
                    or is_stale(doc, started)
                )
                pending = doc.status in (Document.Status.QUEUED, Document.Status.PROCESSING)
                doc.seen_at = started
                if new_content or doc.status == Document.Status.DELETED:
                    doc.attempts = 0
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
                if not pending:  # nothing will read what the connector wrote for it
                    remove_work_file(item.metadata.get("path"))
        note = ""
        if full:
            note = _delete_missing(source, connector, started, counts, run_id)
        finish_run(
            source_id,
            run_id,
            status=Source.Status.IDLE,
            last_run_at=started,
            last_error=note[:2000],
            cursor=Cursor(value="full", issued_at=started).model_dump(mode="json"),
        )
    except RunLost:
        log.warning("discover_source %s: released during the run by `recover`; stopped", source_id)
        return counts
    except Exception as exc:  # noqa: BLE001
        log.exception("discover_source %s failed", source_id)
        finish_run(
            source_id,
            run_id,
            status=Source.Status.FAILED,
            last_run_at=started,  # a failing scheduled source waits for its next turn
            last_error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        raise
    return counts


def _delete_missing(
    source: Source, connector: Any, started: datetime, counts: dict[str, int], run_id: str
) -> str:
    """Delete what a complete full run did not see; the reason when nothing is deleted."""

    from oss_pipeline.connectors.base import deletes_missing, report_of
    from oss_server.pipeline.runs import holds

    report = report_of(connector)
    if report is not None and report.unchecked:
        return (
            f"{len(report.unchecked)} item(s) could not be read on this run, so nothing was "
            f"deleted; the first: {report.unchecked[0]}"
        )
    if report is not None and report.truncated:
        return ""  # stopped at a limit: what lies past it was not looked at, not deleted
    if not deletes_missing(connector, source.settings):
        return ""
    # not seen by this run, and not touched since it started (an upload, a newer run)
    gone = (
        Document.objects.filter(source=source)
        .exclude(status=Document.Status.DELETED)
        .filter(Q(seen_at__lt=started) | Q(seen_at__isnull=True))
        .filter(updated_at__lt=started)
    )
    ids = list(gone.values_list("id", flat=True))
    if not ids:
        return ""
    try:
        min_yield = float(source.settings.get("min_yield", MIN_YIELD))
    except (TypeError, ValueError):
        min_yield = MIN_YIELD
    before, seen = counts["before"], counts["seen"]
    if min_yield > 0 and (seen == 0 or (before >= MIN_YIELD_FLOOR and seen < min_yield * before)):
        return (
            f"this run saw {seen} of the {before} documents the source had, so the {len(ids)} "
            "it did not see were kept; if they are really gone, set the source's min_yield to "
            "0 and run it again"
        )
    if not holds(source.pk, run_id):
        raise RunLost
    for doc_id in ids:
        delete_document.delay(doc_id, seen_before=started.isoformat())
        counts["deleted"] += 1
    return ""


TASK_RETRIES = 3  # re-runs of a document task after transient failures (settings: task_retries)
TASK_BACKOFF_S = 60.0  # first wait, doubling: 60 s, 120 s, 240 s (settings: task_retry_backoff_s)


# Document tasks: killed after the time limit, and handed back to the queue when their pool
# process dies (out of memory, a crash) rather than acknowledged; `begin` stops a document
# that keeps killing its worker. Valkey's visibility timeout is longer than the time limit
# plus the longest retry wait (oss_pipeline.celery_app).
DOCUMENT_TASK: dict[str, Any] = {
    "queue": "cpu",
    "acks_late": True,
    "reject_on_worker_lost": True,
    "time_limit": get_settings().task_time_limit,
}
DOCUMENT_TASKS = ("semascope.process_document", "semascope.reindex_document")


def _retry_policy(collection: Collection) -> tuple[int, float]:
    pipeline = pipeline_overrides(collection.settings)
    return (
        max(0, int(pipeline.get("task_retries", TASK_RETRIES))),
        float(pipeline.get("task_retry_backoff_s", TASK_BACKOFF_S)),
    )


def _countdown(attempt: int, base: float) -> float:
    """The wait before retry `attempt`, never past what the visibility timeout allows for."""

    return min(backoff(attempt, base), float(MAX_RETRY_COUNTDOWN_S))


def is_stale(doc: Document, now: datetime) -> bool:
    """`processing` for longer than any run may last: its worker died (no live check needed)."""

    if doc.status != Document.Status.PROCESSING:
        return False
    s = get_settings()
    limit = timedelta(seconds=s.task_time_limit + s.semascope_stale_after_s)
    return doc.processing_since is None or doc.processing_since < now - limit


def begin(row: Document, item: dict[str, Any] | None = None) -> bool:
    """Mark a document `processing` for a run, or fail it when its runs keep dying.

    Every start is counted in `attempts`, which a finished run resets, so the count is the
    number of runs in a row that never finished: their worker was killed (out of memory, a
    crash in a native library, the time limit). After `SEMASCOPE_MAX_ATTEMPTS` (3) the
    document is marked failed instead of being run again, so one bad file cannot take the
    workers down for ever; a new version of it, or `oss reindex`, tries again.
    """

    limit = max(1, get_settings().semascope_max_attempts)
    if row.attempts >= limit:
        error = (
            f"interrupted {row.attempts} times: the worker running it was killed (out of "
            f"memory, a crash or the {get_settings().task_time_limit} s time limit); held "
            "until the file changes or `oss reindex`"
        )
        Document.objects.filter(pk=row.pk).update(
            status=Document.Status.FAILED, processing_since=None, error=error
        )
        log.error("document %s: %s", row.pk, error)
        return False
    if row.attempts:
        log.warning("document %s: run %d, the last one did not finish", row.pk, row.attempts + 1)
    updates: dict[str, Any] = {
        "status": Document.Status.PROCESSING,
        "processing_since": timezone.now(),
        "attempts": F("attempts") + 1,
    }
    if item is not None:
        updates["item"] = item
    Document.objects.filter(pk=row.pk).update(**updates)
    row.attempts += 1
    return True


def fingerprint(content_hash: str, ctx: StageContext, stages: list[Stage]) -> str:
    """What a document's index entry depends on: its content and everything that processes it."""

    payload = {
        "content": content_hash,
        "profile": ctx.profile,
        "stages": [[s.name, s.version] for s in stages],
        "packs": ctx.pack_versions,
        "settings": ctx.settings,
    }
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def already_done(row: Document, item: dict[str, Any], fp: str) -> bool:
    """Indexed from this content with these inputs and nothing outstanding: a run would repeat it.

    This is what makes a redelivered task cheap: Valkey hands out again a task whose worker
    died after storing the result but before acknowledging it.
    """

    return (
        row.status == Document.Status.INDEXED
        and bool(row.fingerprint)
        and row.fingerprint == fp
        and not row.error
        and item.get("content_hash") in (None, "", row.content_hash)
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
    if failed == {"graph"} and row.status == Document.Status.INDEXED:
        # searchable already; only the graph projection waits for Neo4j
        if retries:
            project_graph.apply_async((row.pk,), countdown=_countdown(1, base))
        return
    attempt = int(getattr(task.request, "retries", 0) or 0) + 1
    if attempt > retries:
        return
    countdown = _countdown(attempt, base)
    note = f"; retry {attempt}/{retries} in {countdown:.0f}s ({', '.join(sorted(failed))})"
    Document.objects.filter(pk=row.pk).update(
        status=Document.Status.QUEUED if row.status == Document.Status.FAILED else row.status,
        error=(row.error + note)[:2000],
    )
    raise task.retry(countdown=countdown, max_retries=retries)


@shared_task(bind=True, name="semascope.process_document", **DOCUMENT_TASK)
def process_document(self: Any, doc_id: str, item: dict[str, Any], *, force: bool = False) -> str:
    doc_row = Document.objects.select_related("collection", "source").get(pk=doc_id)
    ctx = build_context(doc_row.collection)
    stages = profile(ctx.profile)
    fp = fingerprint(doc_row.content_hash, ctx, stages)
    if not force and already_done(doc_row, item, fp):
        log.info("document %s is indexed with the same content and settings; skipped", doc_id)
        _drop_work_file(item)
        return "unchanged"
    connector = connectors().get(doc_row.source.connector)
    if connector is None:  # a connector plugin that was removed: fail it, never strand it
        Document.objects.filter(pk=doc_id).update(
            status=Document.Status.FAILED,
            error=f"unknown connector {doc_row.source.connector}",
            processing_since=None,
        )
        return str(Document.Status.FAILED)
    if not begin(doc_row, item):
        return str(Document.Status.FAILED)
    try:
        fetched = _fetch(
            connector, SourceItem.model_validate(item), doc_row.source, doc_row.collection.slug
        )
    except Exception as exc:  # noqa: BLE001 - recorded on the document
        retries, base = _retry_policy(doc_row.collection)
        attempt = int(getattr(self.request, "retries", 0) or 0) + 1
        transient = is_transient(exc)
        error = f"fetch: {type(exc).__name__}: {exc}"[:1900]
        done = {"processing_since": None, "attempts": 0}  # the run finished
        if transient and attempt <= retries:
            countdown = _countdown(attempt, base)
            Document.objects.filter(pk=doc_id).update(
                status=Document.Status.QUEUED,
                error=f"{error}; retry {attempt}/{retries} in {countdown:.0f}s",
                **done,
            )
            raise self.retry(exc=exc, countdown=countdown, max_retries=retries) from exc
        Document.objects.filter(pk=doc_id).update(
            status=Document.Status.FAILED, error=error, **done
        )
        return str(Document.Status.FAILED)
    meta = fetched.item.metadata
    fields: dict[str, Any] = {"local_path": fetched.path} if fetched.path else {}
    fields.update(custom_fields(meta.get("fields") or {}))  # x_ keys, never index fields
    if meta.get("parent_uri"):
        fields["x_parent_uri_s"] = str(meta["parent_uri"])
    if meta.get("children_expanded"):  # its attachments are documents of their own
        fields["children_expanded"] = True
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
    try:
        work = _run(stages, work, ctx)
        _store(doc_row, work, fingerprint=fp)
    except Exception as exc:  # noqa: BLE001 - a bug or a store failure: say so on the document
        log.exception("document %s failed outside its stages", doc_id)
        Document.objects.filter(pk=doc_id).update(
            status=Document.Status.FAILED,
            error=f"{type(exc).__name__}: {exc}"[:2000],
            processing_since=None,
            attempts=0,
        )
        return str(Document.Status.FAILED)
    if doc_row.status == Document.Status.INDEXED and doc_row.blob_path:
        _drop_work_file(item)  # the original is in the blob store now
    _retry_if_transient(self, doc_row, work)
    return doc_row.status


def _drop_work_file(item: dict[str, Any]) -> None:
    """Remove what the connector wrote to its work area for this item (nothing else)."""

    from oss_pipeline.connectors.base import remove_work_file

    path = (item.get("metadata") or {}).get("path") if isinstance(item, dict) else None
    remove_work_file(path)


def _fetch(connector: Any, item: SourceItem, source: Source, collection: str) -> Any:
    """Fetch an item; connectors that take `source` get its current settings (credentials)."""

    if "source" in inspect.signature(connector.fetch).parameters:
        config = SourceConfig(
            id=str(source.id),
            collection=collection,
            connector=source.connector,
            settings=source.settings,
        )
        return connector.fetch(item, source=config)
    return connector.fetch(item)


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


@shared_task(bind=True, name="semascope.reindex_document", **DOCUMENT_TASK)
def reindex_document(self: Any, doc_id: str, stages: list[str] | None = None) -> str:
    """Replay enrichment and indexing from the stored extraction; no Tika.

    `stages` limits the replay to those stages of the profile (plus the stages they feed and
    the index and graph stages that publish the result), in profile order: after a change of
    embedding model only `["embed"]` has to be recomputed.
    """

    doc_row = Document.objects.select_related("collection", "source").get(pk=doc_id)
    if not begin(doc_row):
        return str(Document.Status.FAILED)
    work = _working_from_row(doc_row)
    ctx = build_context(doc_row.collection)
    wanted = replay_set(stages)
    if wanted:
        # a partial replay keeps the tags of the stages it does not run and recomputes the rest
        work.tags = [Tag(**t) for t in doc_row.tags if t.get("stage") not in wanted]
    every = profile(ctx.profile)
    todo = [
        s for s in every if s.name not in ("fetch", "extract") and (not wanted or s.name in wanted)
    ]
    # a partial replay publishes nothing when a replayed stage fails: the published result
    # stays as it was and the document reports the failure
    work = _run(todo, work, ctx, stop_on_failure=tuple(wanted - set(PUBLISH)))
    # a full replay is what processing the same content again would give
    fp = "" if wanted else fingerprint(doc_row.content_hash, ctx, every)
    _store(doc_row, work, fingerprint=fp)
    _retry_if_transient(self, doc_row, work)
    return doc_row.status


GRAPH_PAUSE_S = 60.0  # after Neo4j failed transiently, documents skip the graph stage this long
_graph_paused_until = 0.0  # per worker process


def _run(
    stages: list[Stage], work: WorkingDocument, ctx: StageContext, **kwargs: Any
) -> WorkingDocument:
    """Run a profile; while Neo4j is known to be away, leave the graph stage for later.

    A Neo4j outage costs each document the driver's own retries times the stage's (about
    a minute and a half), although the graph is only a projection. So after one transient
    graph failure this process skips the graph stage for `GRAPH_PAUSE_S`: the document is
    indexed with its projection `pending`, and `project_graph` and `recover` write it later.
    """

    global _graph_paused_until
    paused = time.monotonic() < _graph_paused_until
    work = run_profile(
        [s for s in stages if not (paused and s.name == "graph")], work, ctx, **kwargs
    )
    if paused and any(s.name == "graph" for s in stages):
        now = datetime.now(UTC)
        work.stage_runs.append(
            StageRun(
                stage="graph",
                version=next(s.version for s in stages if s.name == "graph"),
                started_at=now,
                duration_ms=0,
                outcome="failed",
                message="Neo4j unavailable a moment ago; the projection is written later",
            )
        )
        work.fields[TRANSIENT_FAILURES] = [*(work.fields.get(TRANSIENT_FAILURES) or []), "graph"]
    elif "graph" in (work.fields.get(TRANSIENT_FAILURES) or []):
        _graph_paused_until = time.monotonic() + GRAPH_PAUSE_S
    return work


def _working_from_row(row: Document) -> WorkingDocument:
    """The stored extraction as a WorkingDocument, for a replay without Tika."""

    return WorkingDocument(
        id=row.id,
        collection=row.collection.slug,
        source_id=str(row.source_id),
        uri=row.uri,
        content_hash=row.content_hash,
        media_type=row.media_type or None,
        language=row.language or None,
        title=row.title or None,
        text=row.text,
        pages=row.pages,
        metadata=dict(row.metadata),
        fields={k: v for k, v in row.fields.items() if k != "indexed"},
    )


@shared_task(name="semascope.reindex_collection", queue="io")
def reindex_collection(slug: str, stages: list[str] | None = None) -> int:
    """Queue a reindex of every indexed document in a collection (after a pack change).

    Also failed documents whose extraction succeeded (a replay may fix them), documents left
    `processing` by a worker that died, and documents held after their runs kept killing the
    worker: an explicit reindex clears their count and tries once more (those without stored
    text are fetched again).
    """

    s = get_settings()
    docs = Document.objects.filter(collection__slug=slug)
    cutoff = timezone.now() - timedelta(seconds=s.task_time_limit + s.semascope_stale_after_s)
    stale = Q(status=Document.Status.PROCESSING) & (
        Q(processing_since__lt=cutoff) | Q(processing_since__isnull=True)
    )
    ids = list(
        docs.filter(Q(status__in=(Document.Status.INDEXED, Document.Status.FAILED)) | stale)
        .exclude(text="")
        .values_list("id", flat=True)
    )
    held = list(
        docs.filter(status=Document.Status.FAILED, text="", attempts__gt=0)
        .exclude(item={})
        .values_list("id", "item")
    )
    Document.objects.filter(pk__in=[*ids, *(h[0] for h in held)]).update(attempts=0)
    for doc_id in ids:
        reindex_document.delay(doc_id, stages or None)
    for doc_id, item in held:
        process_document.delay(doc_id, item, force=True)
    return len(ids) + len(held)


@shared_task(bind=True, name="semascope.project_graph", queue="io")
def project_graph(self: Any, doc_id: str) -> str:
    """Write an indexed document's graph projection again after Neo4j was unavailable.

    Retried with the document tasks' backoff while Neo4j stays away; after that the
    document stays `graph_status=pending` and `recover` projects it once Neo4j answers.
    """

    row = Document.objects.select_related("collection").filter(pk=doc_id).first()
    if row is None or row.status != Document.Status.INDEXED:
        return "skipped"
    if row.graph_status != Document.GraphStatus.PENDING:
        return row.graph_status
    status = _project_graph(row)
    retries, base = _retry_policy(row.collection)
    attempt = int(self.request.retries or 0) + 1
    if status == Document.GraphStatus.PENDING and attempt <= retries:
        raise self.retry(countdown=_countdown(attempt + 1, base), max_retries=retries)
    return status


def _graph_stage(collection: Collection) -> tuple[StageContext, Stage | None]:
    ctx = build_context(collection)
    return ctx, next((s for s in profile(ctx.profile) if s.name == "graph"), None)


def _project_graph(row: Document, prepared: tuple[StageContext, Stage | None] | None = None) -> str:
    """Run the graph stage alone on the stored document; returns the new graph status."""

    ctx, stage = prepared or _graph_stage(row.collection)
    if stage is None:  # the profile has no graph stage (any more)
        Document.objects.filter(pk=row.pk).update(graph_status=Document.GraphStatus.NONE)
        return str(Document.GraphStatus.NONE)
    work = _working_from_row(row)
    work.tags = [Tag(**t) for t in row.tags]
    work = run_stage(stage, work, ctx)
    run = work.stage_runs[-1].model_dump(mode="json")
    status = _graph_status(work, row.graph_status)
    runs = [run if r.get("stage") == "graph" else r for r in row.stage_runs]
    if run not in runs:
        runs.append(run)
    fields = dict(row.fields)
    if "graph_written" in work.fields:
        fields["graph_written"] = work.fields["graph_written"]
    error = "; ".join(
        f"{r.get('stage')}: {r.get('message')}" for r in runs if r.get("outcome") == "failed"
    )[:2000]
    Document.objects.filter(pk=row.pk, status=Document.Status.INDEXED).update(
        graph_status=status, stage_runs=runs, fields=fields, error=error
    )
    return status


RECOVER_BATCH = 1000  # graph re-projections per run at most
RECOVER_BUDGET_S = 60.0  # and for about this long


@shared_task(name="semascope.recover", queue="io")
def recover() -> dict[str, int]:
    """Every two minutes: requeue documents whose worker died; re-project pending graphs.

    A document is `processing` while a worker runs it. When it has been so for longer than
    `SEMASCOPE_STALE_AFTER_S` (300 s) and no live worker reports it among its active tasks,
    or for longer than the time limit allows at all, its worker died: it is queued again
    with the connector item it was given (`begin` then counts the attempt). Sources left
    `running` by a dead worker are marked failed, so the scheduler starts them again.
    Indexed documents whose graph projection is pending are projected again, oldest first,
    as long as Neo4j answers.
    """

    running = _running_tasks()
    return {
        "requeued": _requeue_stale(running),
        "sources": _release_sources(running),
        "projected": _project_pending(),
    }


def _running_tasks() -> dict[str, set[str]] | None:
    """Document and source ids in the active tasks of every worker that answered, or None.

    None when no worker answered in time: then only the time limit says a run is dead.
    """

    from oss_server.celery import app

    try:
        replies = app.control.inspect(timeout=2.0).active()
    except Exception:  # noqa: BLE001 - no broker or no answer: decide by age alone
        log.warning("recover: no answer from the workers", exc_info=True)
        return None
    if not replies:
        return None
    out: dict[str, set[str]] = {"documents": set(), "sources": set()}
    for tasks in replies.values():
        for task in tasks or []:
            args = task.get("args") or []
            first = args[0] if isinstance(args, list | tuple) and args else None
            if task.get("name") in DOCUMENT_TASKS and first is not None:
                out["documents"].add(str(first))
            elif task.get("name") == "semascope.discover_source" and first is not None:
                out["sources"].add(str(first))
    return out


def _requeue_stale(running: dict[str, set[str]] | None) -> int:
    s = get_settings()
    now = timezone.now()
    grace = now - timedelta(seconds=s.semascope_stale_after_s)
    rows = list(
        Document.objects.filter(status=Document.Status.PROCESSING)
        .filter(Q(processing_since__lt=grace) | Q(processing_since__isnull=True))
        .only("id", "processing_since", "item", "status")
    )
    requeued = 0
    for row in rows:
        if running is not None and row.id in running["documents"]:
            continue  # alive, just slow
        if running is None and not is_stale(row, now):
            continue  # no worker answered; it may still be running within its time limit
        claim = Document.objects.filter(
            pk=row.pk, status=Document.Status.PROCESSING, processing_since=row.processing_since
        )
        if not row.item:  # started before 1.0 kept the item: let a source run or reindex do it
            claim.update(
                status=Document.Status.FAILED,
                processing_since=None,
                error="the worker running it stopped; run the source again or `oss reindex`",
            )
            continue
        if claim.update(
            status=Document.Status.QUEUED,
            processing_since=None,
            error="requeued: the worker running it stopped",
        ):
            log.warning("recover: document %s was left processing; queued again", row.pk)
            process_document.delay(row.pk, row.item)
            requeued += 1
    return requeued


def _release_sources(running: dict[str, set[str]] | None) -> int:
    if running is None:
        return 0
    released = 0
    for source in Source.objects.filter(status=Source.Status.RUNNING).only("id"):
        if str(source.pk) in running["sources"]:
            continue
        released += Source.objects.filter(pk=source.pk, status=Source.Status.RUNNING).update(
            status=Source.Status.FAILED, last_error="interrupted: the worker running it stopped"
        )
    return released


def _project_pending() -> int:
    pending = (
        Document.objects.filter(
            status=Document.Status.INDEXED, graph_status=Document.GraphStatus.PENDING
        )
        .select_related("collection")
        .order_by("indexed_at")[:RECOVER_BATCH]
    )
    deadline = time.monotonic() + RECOVER_BUDGET_S
    prepared: dict[int, tuple[StageContext, Stage | None]] = {}
    projected = 0
    for row in pending:
        if row.collection_id not in prepared:
            prepared[row.collection_id] = _graph_stage(row.collection)
        if _project_graph(row, prepared[row.collection_id]) == Document.GraphStatus.PENDING:
            break  # Neo4j still unavailable: the next run tries again
        projected += 1
        if time.monotonic() > deadline:
            break
    return projected


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


KEEP_ON_DELETE = ("curated", "dismissed_violations")  # curator decisions, for a revival

# The document's machine edges go, its curator decisions (CURATED) stay with a bare node;
# then the entities only it mentioned (an NER name is personal data).
GRAPH_DELETE = (
    "MATCH (d:Document {id: $id}) OPTIONAL MATCH (d)-[r]-() WHERE type(r) <> 'CURATED' "
    "DELETE r WITH DISTINCT d "
    "REMOVE d.title, d.source_uri, d.content_hash, d.language, d.media_type, d.indexed_at "
    "WITH d WHERE NOT (d)--() DELETE d"
)
GRAPH_MENTIONED = (
    "MATCH (d:Document {id: $id})-[:MENTIONS]->(e:Entity) RETURN collect(DISTINCT e.key) AS keys"
)
GRAPH_ORPHANS = "UNWIND $keys AS k MATCH (e:Entity {key: k}) WHERE NOT (e)--() DELETE e"


@shared_task(bind=True, name="semascope.delete_document", queue="io")
def delete_document(self: Any, doc_id: str, seen_before: str | None = None) -> str:
    """Delete a document a source no longer has, everywhere: Solr (document and passages),
    the graph (its curator decisions excepted), its blob and previews when no other document
    uses them, and its work file; the Postgres row stays as a tombstone with its content
    cleared, so a later run that sees it again brings it back.

    `seen_before` (the deleting run's start) makes the delete a no-op for a document a
    newer run has seen since.
    """

    from oss_pipeline.connectors.base import remove_work_file
    from oss_server.pipeline.retention import remove_files, tombstone
    from oss_server.services import solr_chunks_collection

    row = Document.objects.filter(pk=doc_id).first()
    if row is None:
        return "missing"
    if seen_before and row.seen_at and row.seen_at >= datetime.fromisoformat(seen_before):
        return "seen again"
    try:
        solr_client().delete_by_id(solr_documents_collection(), [doc_id])
        solr_client().delete_by_query(solr_chunks_collection(), f"document_id:{quote_term(doc_id)}")
    except Exception as exc:
        if is_transient(exc) and self.request.retries < TASK_RETRIES:
            raise self.retry(
                exc=exc, countdown=_countdown(self.request.retries + 1, TASK_BACKOFF_S)
            ) from exc
        raise
    try:
        from oss_server.services import graph_client

        graph = graph_client()
        found = graph.run(GRAPH_MENTIONED, id=doc_id)
        keys = list(found[0]["keys"]) if found else []
        graph.execute(GRAPH_DELETE, id=doc_id)
        if keys:
            graph.execute(GRAPH_ORPHANS, keys=keys)
    except Exception:  # noqa: BLE001 - the graph is a projection; Solr and Postgres are the record
        log.exception("graph delete failed for %s", doc_id)
    kept = {k: row.fields[k] for k in KEEP_ON_DELETE if row.fields.get(k)}
    path = (row.item.get("metadata") or {}).get("path") if isinstance(row.item, dict) else None
    Document.objects.filter(pk=doc_id).update(**{**tombstone(), "fields": kept})
    remove_files([(row.blob_path, row.content_hash)])
    remove_work_file(path)
    return str(Document.Status.DELETED)


@shared_task(bind=True, name="semascope.purge_documents", queue="io")
def purge_documents(self: Any, ids: list[str], files: list[Any]) -> int:
    """After a source or collection was deleted: its documents' files and graph nodes.

    Solr was purged when the source or collection was deleted. The graph purge is retried
    while Neo4j is away, like a document's graph stage."""

    from oss_server.pipeline.retention import purge_graph, remove_files

    removed = remove_files((str(b), str(h)) for b, h in files)
    try:
        purge_graph(ids)
    except Exception as exc:
        if is_transient(exc) and self.request.retries < TASK_RETRIES:
            raise self.retry(
                exc=exc, countdown=_countdown(self.request.retries + 1, TASK_BACKOFF_S)
            ) from exc
        log.exception("graph purge failed for %d documents", len(ids))
    return removed


@shared_task(name="semascope.purge_area", queue="io")
def purge_area(collection: str, source_id: str | None, *, uploads: bool = False) -> None:
    """A deleted source's (or collection's) work area, and its uploaded files when asked."""

    from oss_server.pipeline.retention import remove_area

    remove_area(collection, source_id, uploads=uploads)


@shared_task(name="semascope.gc", queue="io")
def gc() -> dict[str, int]:
    """Once a day: blobs and previews no document uses, stale work files, expired leases."""

    from oss_server.pipeline.retention import gc as sweep
    from oss_server.pipeline.runs import expired_leases

    out = sweep()
    out["leases"] = expired_leases()
    return out


def _graph_status(work: WorkingDocument, previous: str) -> str:
    """The projection's state after a run: the last graph stage run's outcome."""

    run = next((r for r in reversed(work.stage_runs) if r.stage == "graph"), None)
    if run is None:
        return previous
    if run.outcome == "ok":
        return str(Document.GraphStatus.OK)
    if run.outcome == "skipped":
        return str(Document.GraphStatus.NONE)
    if "graph" in (work.fields.get(TRANSIENT_FAILURES) or []):
        return str(Document.GraphStatus.PENDING)
    return str(Document.GraphStatus.FAILED)


def _store(row: Document, work: WorkingDocument, *, fingerprint: str = "") -> None:
    failed = [r for r in work.stage_runs if r.outcome == "failed"]
    indexed = bool(work.fields.get("indexed"))
    _record_metrics(row, work, indexed)
    # the fetch stage hashes what it read: a file that changed after discovery is indexed as
    # what was read, under that hash
    row.content_hash = work.content_hash or row.content_hash
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
    row.graph_status = _graph_status(work, row.graph_status)
    row.fingerprint = fingerprint if indexed else ""
    row.processing_since = None
    row.attempts = 0  # the run finished, whatever its outcome
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


WEBHOOK_BATCH = 100  # events per hook per run
WEBHOOK_TIMEOUT_S = 15.0
# a hook's lease covers a whole batch of slow deliveries; an expired one is free again
WEBHOOK_LEASE_S = WEBHOOK_BATCH * WEBHOOK_TIMEOUT_S + 60
ALERTS_LEASE_S = 600.0


@shared_task(name="semascope.deliver_webhooks", queue="io")
def deliver_webhooks() -> int:
    """Send outbox events to every enabled webhook, in order, with an HMAC signature.

    Delivery goes through the outbound guard (oss_pipeline.connectors.guard): a receiver on
    a private address needs SEMASCOPE_ALLOWED_PRIVATE_HOSTS, and redirects are not followed.
    Each hook is delivered by one run at a time (a lease, `runs.lease`): the tick starts a
    run every minute, and a slow receiver can make one run last longer than that. The
    hook's cursor moves after each delivered event, never backwards, so an event is sent
    once even when a run is killed half way.
    """

    from oss_server.curation.models import Webhook
    from oss_server.pipeline.runs import lease

    sent = 0
    for hook_id in Webhook.objects.filter(enabled=True).values_list("pk", flat=True):
        with lease(f"webhook:{hook_id}", WEBHOOK_LEASE_S) as held:
            if held is None:
                continue  # another run is delivering to this hook
            sent += _deliver(Webhook.objects.get(pk=hook_id))
    return sent


def _advance(hook: Any, event_id: int, error: str = "") -> None:
    """Move a hook's cursor forward to `event_id` (never back) and record the outcome."""

    from oss_server.curation.models import Webhook

    Webhook.objects.filter(pk=hook.pk, last_event_id__lt=event_id).update(
        last_event_id=event_id, last_error=error
    )
    hook.last_event_id = max(hook.last_event_id, event_id)


def _deliver(hook: Any) -> int:
    """Deliver a hook's next batch of events, in order, stopping at the first failure."""

    import hmac

    import httpx

    from oss_pipeline.connectors.guard import SourceNotAllowed, guarded_client
    from oss_server.curation.models import Event, Webhook

    sent = 0
    events = Event.objects.filter(pk__gt=hook.last_event_id).order_by("pk")[:WEBHOOK_BATCH]
    for event in events:
        if hook.kinds and not any(event.kind.startswith(k) for k in hook.kinds):
            _advance(hook, event.pk)
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
        error = ""
        try:
            with guarded_client(timeout=WEBHOOK_TIMEOUT_S, follow_redirects=False) as client:
                resp = client.post(hook.url, content=body, headers=headers)
            if resp.is_redirect:
                error = f"{resp.status_code} on event {event.pk} (not followed)"
            elif resp.status_code >= 400:
                error = f"{resp.status_code} on event {event.pk}"
        except SourceNotAllowed as exc:
            error = f"refused on event {event.pk}: {exc}"[:2000]
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__} on event {event.pk}"
        if error:
            Webhook.objects.filter(pk=hook.pk).update(last_error=error)
            break
        _advance(hook, event.pk)
        sent += 1
    return sent


@shared_task(name="semascope.check_alerts", queue="io")
def check_alerts() -> int:
    """Run every saved search with an alert; emit an event when its total grew.

    The event is in the shared outbox, which every reader of the collection and every
    webhook sees, so it says only that saved search `<id>` of collection `<c>` has `new`
    more matches: never its owner, its name, its query or its hits. The owner reads those in
    the in-app alert list (`/api/views/alerts`). One check runs at a time (a lease).
    """

    from oss_server.api import search as search_mod
    from oss_server.api.access import can_read, readable_collections
    from oss_server.api.schemas import SearchIn
    from oss_server.catalogue.models import SavedSearch
    from oss_server.curation.services import emit
    from oss_server.pipeline.runs import lease

    fired = 0
    with lease("check_alerts", ALERTS_LEASE_S) as held:
        if held is None:
            return 0  # the previous check is still running
        for saved in SavedSearch.objects.filter(alert=True).select_related("user"):
            try:
                req = SearchIn(**saved.query).model_copy(
                    update={"page": 1, "page_size": 5, "facets": []}
                )
                # an alert sees what its owner may read now, not what they could when saving it
                if req.collection and not can_read(saved.user, req.collection):
                    continue
                readable = readable_collections(saved.user)
                allowed = None if readable is None else sorted(readable)
                res = search_mod.search(solr_client(), solr_documents_collection(), req, allowed)
            except Exception:  # noqa: BLE001
                log.exception("alert %s failed", saved.pk)
                continue
            if saved.last_checked_at is not None and res.total > saved.last_total:
                emit(
                    "alert.saved_search",
                    collection=str(saved.query.get("collection") or ""),
                    subject=f"saved-search:{saved.pk}",
                    payload={
                        "saved_search": saved.pk,
                        "new": res.total - saved.last_total,
                        "total": res.total,
                    },
                )
                fired += 1
            saved.last_total = res.total
            saved.last_checked_at = timezone.now()
            saved.save(update_fields=["last_total", "last_checked_at"])
    return fired
