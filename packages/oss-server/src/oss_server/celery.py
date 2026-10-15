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

__all__ = ["app"]
