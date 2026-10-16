"""The Celery application: one app, two queues.

`cpu` holds the document tasks (`process_document`, `reindex_document`), each of which
runs a collection's whole profile in one worker process; `io` holds everything light
(discovery, deletes, graph re-projection, the scheduler tick, recovery, webhooks and
alerts), so those never wait behind a backlog of documents. Stages do not have queues of
their own (docs/adr/0009-one-task-per-document.md).

Delivery is at least once: a task is acknowledged when it ends (`task_acks_late`), so a
worker that dies leaves it in Valkey, which hands it out again after
`broker_transport_options.visibility_timeout`. The timeout is longer than the hard time
limit of a document task plus its longest retry wait, so a task that is still running is
never handed to a second worker. See docs/operations.md#crashes-and-recovery.
"""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from oss_core.config import get_settings

QUEUES = ("io", "cpu")

settings = get_settings()

app = Celery("semascope", broker=settings.valkey_url, backend=settings.valkey_url)
app.conf.update(
    task_queues=[Queue(name) for name in QUEUES],
    task_default_queue="cpu",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": settings.visibility_timeout},
    result_backend_transport_options={"visibility_timeout": settings.visibility_timeout},
    # on SIGTERM (`docker compose stop`, a deploy) wait this long for running tasks, then
    # hand the unfinished ones back to Valkey at once instead of after the visibility timeout;
    # shorter than the worker's stop_grace_period in compose.yaml
    worker_soft_shutdown_timeout=45.0,
    worker_eta_task_limit=1000,
)
