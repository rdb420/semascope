"""Document tasks retry transient failures a bounded number of times; permanent ones fail."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from oss_core.contracts import StageRun, WorkingDocument
from oss_pipeline.stages.base import TRANSIENT_FAILURES
from oss_server.catalogue.models import Collection, Document, Source
from oss_server.pipeline import tasks

REQ = httpx.Request("GET", "http://svc")


class RetryError(Exception):
    pass


class FakeTask:
    def __init__(self, retries: int) -> None:
        self.request = type("Req", (), {"retries": retries})()
        self.calls: list[dict[str, Any]] = []

    def retry(self, **kwargs: Any) -> RetryError:
        self.calls.append(kwargs)
        return RetryError()


@pytest.fixture
def row(db: None) -> Document:
    c = Collection.objects.create(slug="rt", name="rt")
    s = Source.objects.create(collection=c, name="s", connector="filesystem")
    return Document.objects.create(
        id="f" * 32,
        collection=c,
        source=s,
        uri="file:///r",
        content_hash="h",
        status=Document.Status.FAILED,
        error="graph: ServiceUnavailable",
    )


def _work(failed: list[str], transient: list[str]) -> WorkingDocument:
    now = datetime.now(UTC)
    work = WorkingDocument(id="f" * 32, collection="rt", source_id="1", uri="u", content_hash="h")
    work.stage_runs = [
        StageRun(stage=s, version="1", started_at=now, duration_ms=1, outcome="failed")
        for s in failed
    ]
    if transient:
        work.fields[TRANSIENT_FAILURES] = transient
    return work


def test_only_transient_failures_requeue_the_document(row: Document) -> None:
    task = FakeTask(retries=0)
    with pytest.raises(RetryError):
        tasks._retry_if_transient(task, row, _work(["graph"], ["graph"]))
    assert task.calls[0]["max_retries"] == tasks.TASK_RETRIES
    assert 45 <= task.calls[0]["countdown"] <= 75
    row.refresh_from_db()
    assert row.status == Document.Status.QUEUED and "retry 1/3" in row.error


def test_no_retry_with_a_permanent_failure_or_none_left(row: Document) -> None:
    task = FakeTask(retries=0)
    tasks._retry_if_transient(task, row, _work(["extract", "graph"], ["graph"]))
    tasks._retry_if_transient(task, row, _work([], []))
    tasks._retry_if_transient(FakeTask(retries=3), row, _work(["graph"], ["graph"]))
    assert task.calls == []
    row.refresh_from_db()
    assert row.status == Document.Status.FAILED


def test_a_collection_can_tune_the_retries(row: Document) -> None:
    row.collection.settings = {"pipeline": {"task_retries": 0}}
    row.collection.save()
    task = FakeTask(retries=0)
    tasks._retry_if_transient(task, row, _work(["graph"], ["graph"]))
    assert task.calls == []


class FailingConnector:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def fetch(self, item: Any) -> Any:
        raise self.exc


def _process(
    row: Document, exc: BaseException, monkeypatch: pytest.MonkeyPatch, retries: int
) -> Any:
    monkeypatch.setattr(tasks, "connectors", lambda: {"filesystem": FailingConnector(exc)})
    tasks.process_document.push_request(retries=retries)  # type: ignore[call-arg]
    try:
        return tasks.process_document(row.id, {"uri": row.uri})
    finally:
        tasks.process_document.pop_request()


def test_a_transient_fetch_failure_is_retried(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    # called directly (not by a worker), Celery's retry re-raises the error
    with pytest.raises(httpx.ConnectError):
        _process(row, httpx.ConnectError("refused", request=REQ), monkeypatch, retries=0)
    row.refresh_from_db()
    assert row.status == Document.Status.QUEUED and "retry 1/3" in row.error


def test_a_fetch_failure_marks_the_document_failed(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _process(row, FileNotFoundError("gone"), monkeypatch, retries=0) == "failed"
    row.refresh_from_db()
    assert row.status == Document.Status.FAILED and row.error.startswith("fetch: FileNotFound")
    # retries used up: a transient failure is final too
    _process(row, httpx.ConnectError("refused", request=REQ), monkeypatch, retries=3)
    row.refresh_from_db()
    assert row.status == Document.Status.FAILED and "ConnectError" in row.error


def test_the_retry_marker_is_not_stored(row: Document) -> None:
    work = _work(["graph"], ["graph"])
    tasks._store(row, work)
    row.refresh_from_db()
    assert TRANSIENT_FAILURES not in row.fields
