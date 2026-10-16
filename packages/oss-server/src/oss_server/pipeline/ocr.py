"""OCR later: the low-priority second pass of a document indexed without OCR.

A collection with `settings.pipeline.ocr: later` indexes a scanned document's text layer
and metadata at once and marks it `ocr_pending` (`oss_pipeline.ocr_later`). Once stored,
`process_document` queues `ocr_document` on the `cpu` queue at the lowest priority
(`OCR_PRIORITY`): a worker takes it only when no ordinary document task is waiting, so a
large import becomes searchable first and is OCRed afterwards.

The second pass runs `extract` again from the stored original with OCR on, then every stage
after it, and stores the result as the document's processing run would have; the
fingerprint stays the one the collection's settings give, so a later source run finds the
document done. When the OCR pass fails for good, the text-layer result stays searchable,
the document is no longer pending and says why. A pending document whose task was lost
(Valkey flushed, say) is queued again by a full `oss reindex` of its collection
(docs/adr/0014-ocr-later.md).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from celery import shared_task
from django.db.models import Q, QuerySet

from oss_core.contracts import Tag
from oss_pipeline.ocr_later import PENDING, PENDING_FIELD, mark_pending
from oss_pipeline.registry import profile
from oss_pipeline.stages.base import TRANSIENT_FAILURES, run_profile, run_stage
from oss_server.catalogue.models import Document
from oss_server.pipeline.tasks import (
    DOCUMENT_TASK,
    _countdown,
    _retry_if_transient,
    _retry_policy,
    _run,
    _store,
    _working_from_row,
    begin,
    build_context,
    fingerprint,
)

log = logging.getLogger(__name__)

OCR_TASK = "semascope.ocr_document"
# Redis priorities run 0 (first) to 9 (last); tasks sent without one are 0
OCR_PRIORITY = 9


def pending(documents: QuerySet[Document] | None = None) -> QuerySet[Document]:
    """Indexed documents whose OCR pass is still to come."""

    qs = documents if documents is not None else Document.objects.all()
    return qs.filter(Q(status=Document.Status.INDEXED) & Q(**{f"metadata__{PENDING}": True}))


def queue_ocr(row: Document) -> bool:
    """Queue the OCR pass of a document stored as `ocr_pending`; True when queued."""

    if row.status != Document.Status.INDEXED or not (row.metadata or {}).get(PENDING):
        return False
    ocr_document.apply_async((row.pk, row.content_hash), priority=OCR_PRIORITY)
    return True


def _still_pending(row: Document | None, content_hash: str) -> bool:
    return (
        row is not None
        and row.status == Document.Status.INDEXED
        and row.content_hash == content_hash
        and bool((row.metadata or {}).get(PENDING))
    )


def _keep_text_layer(row: Document, reason: str) -> str:
    """The OCR pass failed for good: publish the text-layer result as no longer pending."""

    ctx = build_context(row.collection)
    work = _working_from_row(row)
    work.tags = [Tag(**t) for t in row.tags]
    mark_pending(work, False)
    work.metadata["ocr_error"] = reason[:500]
    index = [s for s in profile(ctx.profile) if s.name == "index"]
    work = run_profile(index, work, ctx, stop_on_failure=())
    published = bool(work.fields.get("indexed"))
    Document.objects.filter(pk=row.pk).update(
        status=Document.Status.INDEXED,
        processing_since=None,
        attempts=0,
        metadata=work.metadata if published else {**row.metadata, "ocr_error": reason[:500]},
        fields={k: v for k, v in work.fields.items() if k != TRANSIENT_FAILURES}
        if published
        else row.fields,
        error=f"ocr: {reason}"[:2000],
    )
    log.warning("document %s: OCR pass failed, text layer kept: %s", row.pk, reason)
    return "ocr failed"


@shared_task(bind=True, name=OCR_TASK, priority=OCR_PRIORITY, **DOCUMENT_TASK)
def ocr_document(self: Any, doc_id: str, content_hash: str) -> str:
    """OCR a document indexed with `ocr: later`, then replay the stages after `extract`."""

    row = Document.objects.select_related("collection", "source").filter(pk=doc_id).first()
    if row is None or not _still_pending(row, content_hash):
        return "skipped"  # changed, deleted, or OCRed already (a duplicate task)
    if not row.blob_path or not Path(row.blob_path).exists():
        return _keep_text_layer(row, "the stored original is gone; run the source again")
    if not begin(row):
        return str(Document.Status.FAILED)
    ctx = build_context(row.collection)
    every = profile(ctx.profile)
    fp = fingerprint(row.content_hash, ctx, every)  # what the collection's settings give
    with_ocr = ctx.model_copy(update={"settings": {**ctx.settings, "ocr": "now"}})
    work = _working_from_row(row)
    work.text, work.pages, work.tags = None, [], []
    work.fields.pop(PENDING_FIELD, None)
    work.fields["blob_path"] = row.blob_path
    todo = [s for s in every if s.name != "fetch"]
    extract = next((s for s in todo if s.name == "extract"), None)
    if extract is None:
        return _keep_text_layer(row, f"profile {ctx.profile} has no extract stage")
    work = run_stage(extract, work, with_ocr)
    last = work.stage_runs[-1]
    if last.outcome != "ok":
        retries, base = _retry_policy(row.collection)
        attempt = int(getattr(self.request, "retries", 0) or 0) + 1
        if "extract" in (work.fields.get(TRANSIENT_FAILURES) or []) and attempt <= retries:
            Document.objects.filter(pk=row.pk).update(
                status=Document.Status.INDEXED, processing_since=None, attempts=0
            )
            raise self.retry(
                countdown=_countdown(attempt, base), max_retries=retries, priority=OCR_PRIORITY
            )
        return _keep_text_layer(row, str(last.message or "extract failed"))
    rest = [s for s in todo if s.name != "extract"]
    work = _run(rest, work, with_ocr)
    current = Document.objects.filter(pk=row.pk).values_list("content_hash", flat=True).first()
    if current != content_hash:
        return "superseded"  # a newer version arrived meanwhile; its own run stores it
    _store(row, work, fingerprint=fp)
    _retry_if_transient(self, row, work)
    return str(row.status)
