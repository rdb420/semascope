"""Celery tasks for curation: the sweep that re-applies decisions a store missed, and bulk
tagging jobs."""

from __future__ import annotations

from celery import shared_task


@shared_task(name="semascope.retry_proposals", queue="io")
def retry_unapplied_proposals() -> int:
    """Apply again the decisions Solr or the graph missed (runs every few minutes)."""

    from oss_server.curation.services import retry_unapplied

    return retry_unapplied()


@shared_task(name="semascope.bulk_tag", queue="io")
def run_bulk_tag_job(job_id: int) -> str:
    """Apply one tag decision to every document a search finds (`curation.bulk`)."""

    from oss_server.curation.bulk import run_bulk_job
    from oss_server.curation.models import BulkTagJob

    job = BulkTagJob.objects.select_related("collection", "created_by").get(pk=job_id)
    if job.status != BulkTagJob.Status.QUEUED:
        return str(job.status)
    return str(run_bulk_job(job).status)
