"""Pack work that runs in a worker: imports, activations, and retagging after a new label.

The API validates a request and shows the diff and reindex plan in the request;
what takes time (loading the triple store, deriving the vocabulary, running the
competency questions, syncing the Solr dictionary, queuing the reindex) runs here,
recorded on a `PackJob` the UI and `oss pack` follow by id.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone

from oss_server.knowledge.models import Pack, PackJob

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(hours=1)  # a queued or running job this old no longer blocks its pack


def on_pack_activated(pack: Pack) -> None:
    """Called once a pack version is active and its dictionary synced, before the reindex.

    The place for work that follows the active vocabulary (for example query-time
    synonyms from the pack's labels). Runs in the worker for API activations and in
    `prepare` for the bundled packs. It must not raise for a store that is down:
    the activation has happened; log and let a later activation catch up.
    """

    log.info("pack %s@%s is active", pack.pack_id, pack.version)


def busy(pack_id: str) -> PackJob | None:
    """A job on this pack that is still queued or running (and not stale), if any."""

    since = timezone.now() - STALE_AFTER
    return (
        PackJob.objects.filter(
            pack_id=pack_id,
            status__in=(PackJob.Status.QUEUED, PackJob.Status.RUNNING),
            created_at__gte=since,
        )
        .order_by("pk")
        .first()
    )


def reindex_collections_using(pack_id: str) -> list[str]:
    """Queue a reindex of every collection that tags with this pack (bound, or unbound = all)."""

    from oss_server.knowledge.upgrade import collections_using
    from oss_server.pipeline.tasks import reindex_collection

    slugs = collections_using(pack_id)
    for slug in slugs:
        reindex_collection.delay(slug)
    return slugs


def _finish_activation(pack: Pack, reindex: bool) -> dict[str, Any]:
    from oss_server.services import sync_dictionary

    entries = sync_dictionary(pack.pack_id, pack.version)
    try:
        on_pack_activated(pack)
    except Exception:  # noqa: BLE001 - the activation stands; the hook's work can be redone
        log.exception("on_pack_activated failed for %s", pack)
    reindexed = reindex_collections_using(pack.pack_id) if reindex else []
    return {"dictionary_entries": entries, "reindexed": reindexed}


def _pack_result(pack: Pack) -> dict[str, Any]:
    return {
        "pack_id": pack.pack_id,
        "version": pack.version,
        "status": pack.status,
        "counts": pack.counts,
        "cq_results": {k: pack.cq_results.get(k) for k in ("tests", "passed")},
    }


def run_job(job: PackJob) -> PackJob:
    """Run an import or activation job to the end, recording the outcome on the job."""

    from oss_server.curation.services import emit
    from oss_server.knowledge.importer import activate_pack, import_pack

    job.status, job.started_at = PackJob.Status.RUNNING, timezone.now()
    job.save(update_fields=["status", "started_at"])
    try:
        params = job.params
        if job.kind == PackJob.Kind.IMPORT:
            activate = bool(params.get("activate", True))
            pack = import_pack(
                Path(str(params["path"])), activate=activate, force=bool(params.get("force"))
            )
            result = _pack_result(pack)
            if activate:
                result |= _finish_activation(pack, bool(params.get("reindex", True)))
            else:
                from oss_server.services import sync_dictionary

                result["dictionary_entries"] = sync_dictionary(pack.pack_id, pack.version)
        elif job.kind == PackJob.Kind.ACTIVATE:
            pack = Pack.objects.get(pack_id=job.pack_id, version=str(params["version"]))
            with transaction.atomic():
                activate_pack(pack)
            result = _pack_result(pack) | _finish_activation(
                pack, bool(params.get("reindex", True))
            )
        else:
            raise ValueError(f"unknown job kind {job.kind!r}")
    except Exception as exc:  # noqa: BLE001 - the job records the failure for the caller
        log.exception("pack job %s failed", job.pk)
        job.status, job.error = PackJob.Status.FAILED, str(exc)[:4000]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
        return job
    job.pack_id, job.version = pack.pack_id, pack.version
    job.status, job.result, job.finished_at = PackJob.Status.DONE, result, timezone.now()
    job.save(update_fields=["pack_id", "version", "status", "result", "finished_at"])
    emit(
        f"pack.{job.kind}",
        subject=f"{pack.pack_id}@{pack.version}",
        payload={
            "job_id": job.pk,
            "status": pack.status,
            "by": getattr(job.created_by, "username", None),
            "reindexed": result.get("reindexed", []),
        },
    )
    return job


# --- retagging after a curator's label ------------------------------------------------------------

RETAG_CAP = 10_000  # documents retagged one by one; more than this reindexes the collection


def retag_for_label(collection_slug: str, label: str) -> int:
    """Queue the tag stage (and what follows it) for the collection's documents whose text
    or title contains the label, so a curator's new name tags them without a full reindex.
    Returns how many were queued; a label in more than RETAG_CAP documents queues the
    collection's tag stage instead (-1)."""

    from oss_pipeline.clients.solr import quote_term
    from oss_server.pipeline.tasks import reindex_collection, reindex_document
    from oss_server.services import solr_client, solr_documents_collection

    phrase = quote_term(label)
    ids: list[str] = []
    cursor = "*"
    while True:
        body = solr_client().select(
            solr_documents_collection(),
            q=f"text_txt:{phrase} OR title_txt:{phrase}",
            fq=[f"collection_s:{quote_term(collection_slug)}"],
            fl="id",
            rows=500,
            sort="id asc",
            cursorMark=cursor,
            hl=False,
        )
        ids += [str(d["id"]) for d in body.get("response", {}).get("docs", [])]
        if len(ids) > RETAG_CAP:
            reindex_collection.delay(collection_slug, ["tag"])
            return -1
        following = str(body.get("nextCursorMark") or cursor)
        if following == cursor:
            break
        cursor = following
    for doc_id in ids:
        reindex_document.delay(doc_id, ["tag"])
    return len(ids)
