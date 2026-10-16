"""The Celery app every process shares: broker from settings, tasks from oss_server.pipeline.

Imported by `oss_server/__init__.py`, so the web process registers the same
app that the worker runs and `task.delay()` reaches the configured broker.
Task discovery is lazy, so importing this module needs no Django set-up.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oss_server.settings")

from oss_pipeline.celery_app import app  # noqa: E402

app.autodiscover_tasks(["oss_server.pipeline"])
app.conf.beat_schedule = {
    "tick": {"task": "semascope.tick", "schedule": 60.0, "options": {"queue": "io"}},
    # requeue documents whose worker died, re-project graphs that waited for Neo4j
    "recover": {"task": "semascope.recover", "schedule": 120.0, "options": {"queue": "io"}},
    # blobs and previews no document uses, stale work files (pipeline.retention.gc)
    "gc": {"task": "semascope.gc", "schedule": 86400.0, "options": {"queue": "io"}},
}

__all__ = ["app"]
