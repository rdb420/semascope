"""The Celery application: one app, five queues routed by cost."""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from oss_core.config import get_settings

settings = get_settings()

app = Celery("semascope", broker=settings.valkey_url, backend=settings.valkey_url)
app.conf.update(
    task_queues=[Queue(name) for name in ("io", "cpu", "ocr", "nlp", "graph")],
    task_default_queue="cpu",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    worker_eta_task_limit=1000,
)
