"""Celery tasks for packs: import and activation jobs, and retagging after a new label."""

from __future__ import annotations

from celery import shared_task


@shared_task(name="semascope.pack_job", queue="io")
def run_pack_job(job_id: int) -> str:
    """Run a queued pack import or activation (`oss_server.knowledge.jobs.run_job`)."""

    from oss_server.knowledge.jobs import run_job
    from oss_server.knowledge.models import PackJob

    job = PackJob.objects.select_related("created_by").get(pk=job_id)
    if job.status != PackJob.Status.QUEUED:
        return str(job.status)  # delivered twice: the first delivery ran it
    return str(run_job(job).status)


@shared_task(name="semascope.retag_label", queue="io")
def retag_label(collection_slug: str, label: str) -> int:
    """Queue the tag stage for the collection's documents that contain a new draft label."""

    from oss_server.knowledge.jobs import retag_for_label

    return retag_for_label(collection_slug, label)
