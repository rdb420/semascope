"""Worker reliability: crash-loop guard, stale recovery, no-op reprocessing, pending graphs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import pytest
from celery.exceptions import Retry
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone
from neo4j.exceptions import ServiceUnavailable

from oss_core.config import get_settings
from oss_core.contracts import StageContext, StageRun, Tag, WorkingDocument
from oss_pipeline.registry import profile
from oss_pipeline.stages.base import TRANSIENT_FAILURES
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, Document, Source, document_id
from oss_server.pipeline import tasks

ITEM: dict[str, Any] = {
    "uri": "file:///import/a.txt",
    "content_hash": "h1",
    "metadata": {"path": "/x/a.txt"},
}
ID = document_id("wr", str(ITEM["uri"]))


@pytest.fixture
def row(db: None) -> Document:
    c = Collection.objects.create(slug="wr", name="wr")
    s = Source.objects.create(collection=c, name="s", connector="filesystem")
    return Document.objects.create(
        id=ID, collection=c, source=s, uri=ITEM["uri"], content_hash="h1", text="some text"
    )


class Calls:
    """Stands in for `task.delay` / `task.apply_async` and records the calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))


def _run(name: str, outcome: Literal["ok", "skipped", "failed"] = "ok") -> StageRun:
    return StageRun(
        stage=name, version="1", started_at=datetime.now(UTC), duration_ms=1, outcome=outcome
    )


# --- crash-loop guard -----------------------------------------------------------------------


def test_each_start_is_counted_and_a_finished_run_resets_the_count(row: Document) -> None:
    assert tasks.begin(row, ITEM)
    row.refresh_from_db()
    assert row.status == Document.Status.PROCESSING and row.attempts == 1
    assert row.processing_since is not None and row.item == ITEM
    work = WorkingDocument(id=ID, collection="wr", source_id="1", uri="u", content_hash="h1")
    work.fields["indexed"] = True
    tasks._store(row, work, fingerprint="f")
    row.refresh_from_db()
    assert (row.status, row.attempts) == ("indexed", 0) and row.processing_since is None


def test_a_document_that_keeps_killing_its_worker_ends_failed(row: Document) -> None:
    for _ in range(3):  # three runs started, none finished: the worker died each time
        assert tasks.begin(row, ITEM)
    assert not tasks.begin(row, ITEM)
    row.refresh_from_db()
    assert row.status == Document.Status.FAILED and row.attempts == 3
    assert "interrupted 3 times" in row.error and row.processing_since is None


def test_a_held_document_is_not_requeued_by_discovery_until_it_changes(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_core.contracts import SourceItem

    Document.objects.filter(pk=ID).update(status=Document.Status.FAILED, attempts=3)
    queued = Calls()
    monkeypatch.setattr(tasks.process_document, "delay", queued)
    monkeypatch.setattr(tasks.delete_document, "delay", Calls())

    class Connector:
        content_hash = "h1"

        def discover(self, config: Any, since: Any) -> Any:
            yield SourceItem(uri=ITEM["uri"], content_hash=self.content_hash)

    connector = Connector()
    monkeypatch.setattr(tasks, "connectors", lambda: {"filesystem": connector})
    monkeypatch.setattr("oss_pipeline.connectors.guard.check_source", lambda *a: None)
    assert tasks.discover_source(row.source_id)["queued"] == 0
    connector.content_hash = "h2"  # a new version of the file gets its chance
    assert tasks.discover_source(row.source_id)["queued"] == 1
    row.refresh_from_db()
    assert row.attempts == 0 and len(queued.calls) == 1


def test_reindex_clears_the_count_and_fetches_held_documents_again(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    Document.objects.filter(pk=ID).update(
        status=Document.Status.FAILED, attempts=3, text="", item=ITEM
    )
    reindexed, processed = Calls(), Calls()
    monkeypatch.setattr(tasks.reindex_document, "delay", reindexed)
    monkeypatch.setattr(tasks.process_document, "delay", processed)
    assert tasks.reindex_collection("wr") == 1
    assert processed.calls == [((ID, ITEM), {"force": True})] and reindexed.calls == []
    row.refresh_from_db()
    assert row.attempts == 0


def test_document_tasks_go_back_to_the_queue_when_their_process_dies() -> None:
    for task in (tasks.process_document, tasks.reindex_document):
        assert task.reject_on_worker_lost is True and task.acks_late is True
        assert task.time_limit == get_settings().task_time_limit
        assert task.queue == "cpu"


# --- stale `processing` ---------------------------------------------------------------------


def _processing(since: datetime | None, item: dict[str, Any] | None = None) -> None:
    Document.objects.filter(pk=ID).update(
        status=Document.Status.PROCESSING, processing_since=since, item=item or ITEM
    )


def test_a_document_no_worker_is_running_is_requeued(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued = Calls()
    monkeypatch.setattr(tasks.process_document, "delay", queued)
    _processing(timezone.now() - timedelta(minutes=10))
    assert tasks._requeue_stale({"documents": {"b" * 32}, "sources": set()}) == 1
    assert queued.calls == [((ID, ITEM), {})]
    row.refresh_from_db()
    assert row.status == Document.Status.QUEUED and "worker running it stopped" in row.error


def test_a_document_still_running_or_just_started_is_left_alone(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued = Calls()
    monkeypatch.setattr(tasks.process_document, "delay", queued)
    _processing(timezone.now() - timedelta(minutes=10))
    assert tasks._requeue_stale({"documents": {ID}, "sources": set()}) == 0  # a slow book
    _processing(timezone.now() - timedelta(seconds=30))
    assert tasks._requeue_stale({"documents": set(), "sources": set()}) == 0  # within grace
    assert queued.calls == []


def test_without_answers_from_workers_only_the_time_limit_decides(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued = Calls()
    monkeypatch.setattr(tasks.process_document, "delay", queued)
    _processing(timezone.now() - timedelta(minutes=10))
    assert tasks._requeue_stale(None) == 0
    limit = get_settings().task_time_limit + get_settings().semascope_stale_after_s
    _processing(timezone.now() - timedelta(seconds=limit + 60))
    assert tasks._requeue_stale(None) == 1


def test_a_stuck_document_without_its_item_is_failed_for_a_source_run(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks.process_document, "delay", Calls())
    Document.objects.filter(pk=ID).update(status=Document.Status.PROCESSING, item={})
    assert tasks._requeue_stale(None) == 0  # rc.2 left it so: no start time, no item
    row.refresh_from_db()
    assert row.status == Document.Status.FAILED and "run the source again" in row.error


def test_reindex_and_source_runs_take_stale_processing_documents(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    reindexed = Calls()
    monkeypatch.setattr(tasks.reindex_document, "delay", reindexed)
    _processing(timezone.now() - timedelta(minutes=2))
    assert tasks.reindex_collection("wr") == 0  # may still be running
    _processing(None)
    assert tasks.reindex_collection("wr") == 1
    row.refresh_from_db()
    assert tasks.is_stale(row, timezone.now())


def test_a_source_left_running_by_a_dead_worker_is_released(row: Document) -> None:
    Source.objects.filter(pk=row.source_id).update(status=Source.Status.RUNNING)
    assert tasks._release_sources(None) == 0
    assert tasks._release_sources({"documents": set(), "sources": {str(row.source_id)}}) == 0
    assert tasks._release_sources({"documents": set(), "sources": set()}) == 1
    assert Source.objects.get(pk=row.source_id).status == Source.Status.FAILED


def test_running_tasks_reads_the_workers_active_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    from oss_server.celery import app

    class Inspect:
        def active(self) -> dict[str, list[dict[str, Any]]]:
            return {
                "w1": [
                    {"name": "semascope.process_document", "args": [ID, ITEM]},
                    {"name": "semascope.discover_source", "args": [7]},
                ],
                "w2": [{"name": "semascope.reindex_document", "args": ["b" * 32, None]}],
            }

    monkeypatch.setattr(app.control, "inspect", lambda **kw: Inspect())
    assert tasks._running_tasks() == {"documents": {ID, "b" * 32}, "sources": {"7"}}
    monkeypatch.setattr(app.control, "inspect", lambda **kw: type("I", (), {"active": dict})())
    assert tasks._running_tasks() is None


# --- reprocessing a finished document --------------------------------------------------------


def test_reprocessing_an_indexed_document_with_the_same_inputs_is_skipped(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = tasks.build_context(row.collection)
    fp = tasks.fingerprint("h1", ctx, profile(ctx.profile))
    Document.objects.filter(pk=ID).update(status=Document.Status.INDEXED, fingerprint=fp)

    def no_fetch() -> dict[str, Any]:
        raise AssertionError("a redelivered task must not fetch again")

    monkeypatch.setattr(tasks, "connectors", no_fetch)
    assert tasks.process_document(ID, ITEM) == "unchanged"
    row.refresh_from_db()
    assert row.status == Document.Status.INDEXED and row.attempts == 0


def test_new_content_settings_or_a_failure_mean_a_real_run() -> None:
    ctx = StageContext(collection="c", profile="default", settings={"ocr": True})
    stages = profile("default")
    fp = tasks.fingerprint("h1", ctx, stages)
    assert fp == tasks.fingerprint("h1", ctx, stages)
    assert fp != tasks.fingerprint("h2", ctx, stages)
    assert fp != tasks.fingerprint(
        "h1", ctx.model_copy(update={"settings": {"ocr": False}}), stages
    )
    assert fp != tasks.fingerprint(
        "h1", ctx.model_copy(update={"pack_versions": {"p": "2"}}), stages
    )
    assert fp != tasks.fingerprint("h1", ctx, profile("fast"))
    indexed = Document(status=Document.Status.INDEXED, content_hash="h1", fingerprint=fp)
    assert tasks.already_done(indexed, ITEM, fp)
    assert not tasks.already_done(indexed, {**ITEM, "content_hash": "h0"}, fp)
    indexed.error = "embed: ConnectError"
    assert not tasks.already_done(indexed, ITEM, fp)
    assert not tasks.already_done(Document(status=Document.Status.FAILED), ITEM, "")


# --- graph projection pending ----------------------------------------------------------------


def _work_with_graph(
    outcome: Literal["ok", "skipped", "failed"], transient: bool
) -> WorkingDocument:
    work = WorkingDocument(id=ID, collection="wr", source_id="1", uri="u", content_hash="h1")
    work.stage_runs = [_run("index"), _run("graph", outcome)]
    work.fields["indexed"] = True
    if transient:
        work.fields[TRANSIENT_FAILURES] = ["graph"]
    return work


@pytest.mark.parametrize(
    ("outcome", "transient", "expected"),
    [
        ("ok", False, "ok"),
        ("skipped", False, ""),
        ("failed", True, "pending"),
        ("failed", False, "failed"),
    ],
)
def test_graph_status_follows_the_graph_stage(
    outcome: Literal["ok", "skipped", "failed"], transient: bool, expected: str
) -> None:
    assert tasks._graph_status(_work_with_graph(outcome, transient), "ok") == expected
    no_graph = WorkingDocument(id=ID, collection="c", source_id="1", uri="u", content_hash="h")
    assert tasks._graph_status(no_graph, "ok") == "ok"


class FakeTask:
    request = type("Req", (), {"retries": 0})()

    def retry(self, **kwargs: Any) -> Exception:
        raise AssertionError("the document is searchable; only the graph is retried")


def test_neo4j_down_leaves_the_document_searchable_with_its_graph_pending(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    later = Calls()
    monkeypatch.setattr(tasks.project_graph, "apply_async", later)
    work = _work_with_graph("failed", transient=True)
    tasks._store(row, work, fingerprint="f")
    tasks._retry_if_transient(FakeTask(), row, work)
    row.refresh_from_db()
    assert row.status == Document.Status.INDEXED and row.graph_status == "pending"
    assert later.calls and later.calls[0][0] == ((ID,),)


class FakeGraphStage:
    name = "graph"
    version = "1"
    requires = ("tags",)
    provides = ("graph_written",)

    def __init__(self, down: bool) -> None:
        self.down = down
        self.seen: list[list[str]] = []

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        self.seen.append([t.iri for t in doc.tags])
        if self.down:
            raise ServiceUnavailable("neo4j is away")
        doc.fields["graph_written"] = {"concepts": 1, "entities": 0}
        return doc


def _pending(row: Document) -> None:
    tag = Tag(iri="urn:c", label="c", kind="concept", stage="tag").model_dump(mode="json")
    Document.objects.filter(pk=ID).update(
        status=Document.Status.INDEXED,
        graph_status=Document.GraphStatus.PENDING,
        tags=[tag],
        stage_runs=[
            _run("index").model_dump(mode="json"),
            {**_run("graph", "failed").model_dump(mode="json"), "message": "ServiceUnavailable"},
        ],
        error="graph: ServiceUnavailable",
    )
    row.refresh_from_db()


def test_recovery_projects_the_graph_once_neo4j_is_back(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pending(row)
    ctx = StageContext(collection="wr", profile="default", settings={"stage_retries": 0})
    down = FakeGraphStage(down=True)
    monkeypatch.setattr(tasks, "_graph_stage", lambda c: (ctx, down))
    assert tasks._project_pending() == 0  # still away: stays pending, tried next time
    row.refresh_from_db()
    assert row.graph_status == "pending" and row.error.startswith("graph:")
    up = FakeGraphStage(down=False)
    monkeypatch.setattr(tasks, "_graph_stage", lambda c: (ctx, up))
    assert tasks._project_pending() == 1
    row.refresh_from_db()
    assert row.graph_status == "ok" and row.error == "" and up.seen == [["urn:c"]]
    assert [r["outcome"] for r in row.stage_runs] == ["ok", "ok"]
    assert row.fields["graph_written"] == {"concepts": 1, "entities": 0}
    assert tasks.project_graph(ID) == "ok"  # nothing pending: a no-op


def test_project_graph_retries_while_neo4j_is_away(
    row: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pending(row)
    ctx = StageContext(collection="wr", profile="default", settings={"stage_retries": 0})
    monkeypatch.setattr(tasks, "_graph_stage", lambda c: (ctx, FakeGraphStage(down=True)))
    tasks.project_graph.push_request(retries=0)  # type: ignore[call-arg]
    try:
        with pytest.raises(Retry):
            tasks.project_graph(ID)
    finally:
        tasks.project_graph.pop_request()
    tasks.project_graph.push_request(retries=3)  # type: ignore[call-arg]
    try:
        assert tasks.project_graph(ID) == "pending"  # out of retries: `recover` takes over
    finally:
        tasks.project_graph.pop_request()


def test_stats_show_pending_and_failed_graphs_and_held_documents(
    row: Document, client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import root

    monkeypatch.setattr(root, "solr_client", lambda: None)
    monkeypatch.setattr(root, "chunk_models", lambda allowed=None: {})
    _pending(row)
    c = row.collection
    for i, (status, graph, attempts) in enumerate(
        [("indexed", "failed", 0), ("indexed", "ok", 0), ("failed", "", 3), ("failed", "", 0)]
    ):
        Document.objects.create(
            id=f"{i:032d}",
            collection=c,
            source=row.source,
            uri=f"u{i}",
            content_hash="h",
            status=status,
            graph_status=graph,
            attempts=attempts,
        )
    user = User.objects.create_user("ops", password="x" * 16, is_staff=True)
    auth = {"Authorization": f"Bearer {issue_token(user, 'test')}"}
    stats = client.get("/api/stats", headers=auth).json()
    assert stats["graph"] == {"pending": 1, "failed": 1} and stats["interrupted"] == 1
    doc = client.get(f"/api/documents/{ID}", headers=auth).json()
    assert doc["graph_status"] == "pending"
