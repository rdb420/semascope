"""The Celery app every process shares: broker from settings, tasks from oss_server.pipeline,
oss_server.curation, oss_server.knowledge and oss_server.audit.

Imported by `oss_server/__init__.py`, so the web process registers the same
app that the worker runs and `task.delay()` reaches the configured broker.
Task discovery is lazy, so importing this module needs no Django set-up.
"""

from __future__ import annotations

import logging
import os

from celery.signals import after_setup_logger, after_setup_task_logger

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oss_server.settings")

from oss_pipeline.celery_app import app  # noqa: E402

app.autodiscover_tasks(
    ["oss_server.pipeline", "oss_server.curation", "oss_server.knowledge", "oss_server.audit"]
)


@after_setup_logger.connect
@after_setup_task_logger.connect
def _redact_logs(logger: logging.Logger, **_: object) -> None:
    """URL passwords masked in the worker's and beat's log lines (oss_server.logredact)."""

    from oss_server.logredact import redact_handlers

    redact_handlers(logger)


app.conf.beat_schedule = {
    "tick": {"task": "semascope.tick", "schedule": 60.0, "options": {"queue": "io"}},
    # requeue documents whose worker died, re-project graphs that waited for Neo4j
    "recover": {"task": "semascope.recover", "schedule": 120.0, "options": {"queue": "io"}},
    # apply again the curation decisions Solr or Neo4j missed
    "retry-proposals": {
        "task": "semascope.retry_proposals",
        "schedule": 300.0,
        "options": {"queue": "io"},
    },
    # blobs and previews no document uses, stale work files (pipeline.retention.gc)
    "gc": {"task": "semascope.gc", "schedule": 86400.0, "options": {"queue": "io"}},
    # audit entries older than SEMASCOPE_AUDIT_RETENTION_DAYS (oss_server.audit.tasks)
    "audit-purge": {
        "task": "semascope.audit_purge",
        "schedule": 86400.0,
        "options": {"queue": "io"},
    },
}

__all__ = ["app"]
