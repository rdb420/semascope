"""Bulk tagging: one tag decision applied to every document of a search (ANN-04).

The request counts the matches (a dry run shows the count before anything is
decided) and queues a `BulkTagJob`; a worker walks the matches with a Solr
cursor and records one proposal per document through `decide_document_tag`,
exactly as a curator's direct decision on that document would. The search runs
inside the job's collection only, with the same query syntax and filters as
`/api/search`.
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from oss_server.catalogue.models import Document
from oss_server.curation.models import BulkTagJob

log = logging.getLogger(__name__)

MAX_DOCUMENTS = 10_000  # a bulk decision on more documents than this is refused
PAGE = 200
PROGRESS_EVERY = 50


def search_params(collection: str, q: str, filters: dict[str, list[str]]) -> dict[str, Any]:
    """Solr parameters for the search, limited to one collection, ids only."""

    from oss_server.api.schemas import SearchIn
    from oss_server.api.search import build_params

    req = SearchIn(q=q or "*:*", collection=collection, filters=filters, facets=[])
    params = build_params(req, [collection])
    for key in [k for k in params if k.startswith(("facet", "hl"))]:
        params.pop(key)
    params.pop("start", None)
    params.update(fl="id", hl=False, sort="id asc", rows=PAGE)
    return params


def count_matches(collection: str, q: str, filters: dict[str, list[str]]) -> int:
    from oss_server.services import solr_client, solr_documents_collection

    params = search_params(collection, q, filters) | {"rows": 0}
    params.pop("sort", None)
    body = solr_client().select(solr_documents_collection(), **params)
    return int(body.get("response", {}).get("numFound", 0))


def matching_ids(collection: str, q: str, filters: dict[str, list[str]]) -> list[str]:
    """Every matching document id, by cursor (at most MAX_DOCUMENTS + 1)."""

    from oss_server.services import solr_client, solr_documents_collection

    params = search_params(collection, q, filters)
    ids: list[str] = []
    cursor = "*"
    while len(ids) <= MAX_DOCUMENTS:
        body = solr_client().select(solr_documents_collection(), **params, cursorMark=cursor)
        ids += [str(d["id"]) for d in body.get("response", {}).get("docs", [])]
        following = str(body.get("nextCursorMark") or cursor)
        if following == cursor:
            break
        cursor = following
    return ids[: MAX_DOCUMENTS + 1]


def run_bulk_job(job: BulkTagJob) -> BulkTagJob:
    """Decide the tag on every matching document, recording progress on the job."""

    from oss_server.curation.services import decide_document_tag, latest_proposal

    job.status, job.started_at = BulkTagJob.Status.RUNNING, timezone.now()
    job.save(update_fields=["status", "started_at"])
    slug = job.collection.slug
    reason = f"bulk tagging job {job.pk}" + (f": {job.reason}" if job.reason else "")
    try:
        ids = matching_ids(slug, str(job.query.get("q") or ""), job.query.get("filters") or {})
        if len(ids) > MAX_DOCUMENTS:
            raise ValueError(f"the search finds more than {MAX_DOCUMENTS} documents")
        job.matched = len(ids)
        job.save(update_fields=["matched"])
        for i, doc_id in enumerate(ids, 1):
            doc = Document.objects.filter(pk=doc_id, collection=job.collection).first()
            if doc is None:  # in the index, gone from the catalogue
                continue
            before = latest_proposal(job.collection, job.iri, doc)
            p = decide_document_tag(
                doc,
                job.iri,
                job.decision,
                label=job.label,
                kind=job.kind,
                reason=reason,
                user=job.created_by,
            )
            if before is not None and p.pk == before.pk:
                job.unchanged += 1
            else:
                job.decided += 1
            if not p.applied:
                job.unapplied += 1
            if i % PROGRESS_EVERY == 0:
                job.save(update_fields=["decided", "unchanged", "unapplied"])
    except Exception as exc:  # noqa: BLE001 - the job records the failure for the caller
        log.exception("bulk tagging job %s failed", job.pk)
        job.status, job.error = BulkTagJob.Status.FAILED, str(exc)[:4000]
    else:
        job.status = BulkTagJob.Status.DONE
    job.finished_at = timezone.now()
    job.save()
    return job
