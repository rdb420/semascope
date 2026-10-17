"""The audit log's retention sweep, run daily by beat (`oss_server.celery`)."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task

log = logging.getLogger(__name__)


def purge_expired() -> int:
    """Remove the entries older than SEMASCOPE_AUDIT_RETENTION_DAYS (0: keep them all)."""

    from django.utils import timezone

    from oss_core.config import get_settings
    from oss_server.audit.log import record
    from oss_server.audit.models import AuditEntry

    days = get_settings().semascope_audit_retention_days
    if days <= 0:
        return 0
    removed = AuditEntry.objects.purge_before(timezone.now() - timedelta(days=days))
    if removed:
        log.info("audit: removed %d entries older than %d days", removed, days)
        record("audit.purge", detail={"removed": removed, "retention_days": days})
    return removed


@shared_task(name="semascope.audit_purge", queue="io")
def purge_audit() -> int:
    return purge_expired()
