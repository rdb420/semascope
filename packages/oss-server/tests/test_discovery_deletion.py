"""Source runs and deletion: what a run may delete, one run per source, and the reach of a delete.

Each test drives `discover_source` with a stand-in connector (the real connectors' own
reports are tested in oss-pipeline's test_ingest_safety.py) and stand-ins for Solr and
Neo4j; Celery tasks are recorded instead of queued.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from oss_core.contracts import SourceConfig, SourceItem, StageContext, WorkingDocument
from oss_pipeline.connectors.base import RunReport, work_dir
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, Document, Source, document_id
from oss_server.pipeline import retention, runs, tasks


class Calls:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))

    @property
    def first_args(self) -> list[Any]:
        return [c[0][0] for c in self.calls]


class Connector:
    """Yields the URIs it is given, with a report, and raises when told to."""

    def __init__(self, uris: list[str], *, deletes_missing: bool = True) -> None:
        self.uris = uris
        self.deletes_missing = deletes_missing
        self.report = RunReport()
        self.unchecked: list[str] = []
        self.truncated = False
        self.fail_after: int | None = None
        self.during: Any = None  # called after the first item (another run, recover)

    def discover(self, config: Any, since: Any) -> Iterator[SourceItem]:
        self.report = RunReport(truncated=self.truncated)
        for what in self.unchecked:
            self.report.skip(what, "HTTP 503")
        for n, uri in enumerate(self.uris):
            if self.fail_after is not None and n >= self.fail_after:
                raise ConnectionError("the site went away")
            yield SourceItem(uri=uri, content_hash=f"h-{uri}")
            if n == 0 and self.during:
                self.during()


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict[str, Calls]:
    out = {"process": Calls(), "delete": Calls()}
    monkeypatch.setattr(tasks.process_document, "delay", out["process"])
    monkeypatch.setattr(tasks.delete_document, "delay", out["delete"])
    monkeypatch.setattr("oss_pipeline.connectors.guard.check_source", lambda *a: None)
    return out


@pytest.fixture
def source(db: None) -> Source:
    c = Collection.objects.create(slug="dd", name="dd")
    return Source.objects.create(collection=c, name="s", connector="fake")


def _use(monkeypatch: pytest.MonkeyPatch, connector: Connector) -> None:
    monkeypatch.setattr(tasks, "connectors", lambda: {"fake": connector})


def _docs(source: Source, uris: list[str], status: str = Document.Status.INDEXED) -> None:
    for uri in uris:
        Document.objects.create(
            id=document_id(source.collection.slug, uri),
            collection=source.collection,
            source=source,
            uri=uri,
            content_hash=f"h-{uri}",
            status=status,
            title=f"title of {uri}",
        )


def _status(source: Source, uri: str) -> str:
    return str(Document.objects.get(pk=document_id(source.collection.slug, uri)).status)


# --- what a run may delete (SEMA-010, SEMA-034) ------------------------------------------------


def test_a_deleted_document_comes_back_when_the_source_has_it_again(
    source: Source, recorded: dict[str, Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    _docs(source, ["u:a"], status=Document.Status.DELETED)  # same content as before
    _use(monkeypatch, Connector(["u:a"]))
    counts = tasks.discover_source(source.pk)
    assert counts["queued"] == 1 and _status(source, "u:a") == "queued"
    assert recorded["process"].first_args == [document_id("dd", "u:a")]


def test_a_complete_run_deletes_what_it_no_longer_sees(
    source: Source, recorded: dict[str, Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    _docs(source, ["u:a", "u:b"])
    _use(monkeypatch, Connector(["u:a"]))
    assert tasks.discover_source(source.pk)["deleted"] == 1
    assert recorded["delete"].first_args == [document_id("dd", "u:b")]
    source.refresh_from_db()
    assert source.status == "idle" and source.last_error == ""


def test_an_item_that_could_not_be_read_means_nothing_is_deleted(
    source: Source, recorded: dict[str, Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    _docs(source, ["u:a", "u:b"])
    connector = Connector(["u:a"])
    connector.unchecked = ["u:b"]  # answered 503 on this run
    _use(monkeypatch, connector)
    assert tasks.discover_source(source.pk)["deleted"] == 0
    assert recorded["delete"].calls == []
    source.refresh_from_db()
    assert source.status == "idle"
    assert "could not be read" in source.last_error and "nothing was deleted" in source.last_error


def test_a_run_stopped_at_a_limit_deletes_nothing(
    source: Source, recorded: dict[str, Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    _docs(source, ["u:a", "u:b"])
    connector = Connector(["u:a"])
    connector.truncated = True  # max_pages reached
    _use(monkeypatch, connector)
    assert tasks.discover_source(source.pk)["deleted"] == 0 and recorded["delete"].calls == []


def test_a_failing_run_deletes_nothing_and_waits_for_its_next_turn(
    source: Source, recorded: dict[str, Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    _docs(source, ["u:a", "u:b"])
    connector = Connector(["u:a", "u:b"])
    connector.fail_after = 1
    _use(monkeypatch, connector)
    with pytest.raises(ConnectionError):
        tasks.discover_source(source.pk)
    assert recorded["delete"].calls == []
    source.refresh_from_db()
    assert source.status == "failed" and "went away" in source.last_error
    # the schedule counts the failed run, so a failing source is not started every minute
    assert source.last_run_at is not None


def test_feeds_never_delete_by_absence_unless_the_source_asks(
    source: Source, recorded: dict[str, Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    _docs(source, ["u:old", "u:new"])
    _use(monkeypatch, Connector(["u:new"], deletes_missing=False))  # the feed's policy
    assert tasks.discover_source(source.pk)["deleted"] == 0 and recorded["delete"].calls == []
    Source.objects.filter(pk=source.pk).update(settings={"delete_missing": True})
    assert tasks.discover_source(source.pk)["deleted"] == 1


def test_a_run_that_sees_too_little_deletes_nothing(
    source: Source, recorded: dict[str, Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    uris = [f"u:{n}" for n in range(20)]
    _docs(source, uris)
    _use(monkeypatch, Connector(uris[:5]))  # a quarter: a mount half gone, a site half down
    assert tasks.discover_source(source.pk)["deleted"] == 0
    source.refresh_from_db()
    assert "saw 5 of the 20" in source.last_error and "min_yield" in source.last_error
    # the operator confirms: they are really gone
    Source.objects.filter(pk=source.pk).update(settings={"min_yield": 0})
    assert tasks.discover_source(source.pk)["deleted"] == 15


def test_an_empty_run_never_deletes_everything(
    source: Source, recorded: dict[str, Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    _docs(source, ["u:a", "u:b", "u:c"])  # below the floor of ten, but nothing at all was seen
    _use(monkeypatch, Connector([]))
    assert tasks.discover_source(source.pk)["deleted"] == 0 and recorded["delete"].calls == []
    # a small source losing one of three documents is an ordinary deletion
    _use(monkeypatch, Connector(["u:a", "u:b"]))
    assert tasks.discover_source(source.pk)["deleted"] == 1


def test_a_document_queued_a_moment_ago_is_not_queued_again(
    source: Source, recorded: dict[str, Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    _docs(source, ["u:a"], status=Document.Status.QUEUED)
    _use(monkeypatch, Connector(["u:a"]))
    assert tasks.discover_source(source.pk)["queued"] == 0  # its task is waiting (SEMA-063)
    # a task lost with the queue: after the TTL the next run queues it again
    old = timezone.now() - timedelta(seconds=tasks.QUEUED_TTL_S + 60)
    Document.objects.filter(source=source).update(updated_at=old)
    assert tasks.discover_source(source.pk)["queued"] == 1


# --- one run per source (SEMA-012) -----------------------------------------------------------


def test_a_second_run_of_a_running_source_does_nothing(
    source: Source, recorded: dict[str, Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    _docs(source, ["u:a", "u:b"])
    held = runs.claim_run(source.pk)  # the scheduled run, still crawling
    assert held is not None
    _use(monkeypatch, Connector(["u:a"]))
    assert tasks.discover_source(source.pk) == {"busy": 1}
    assert recorded["delete"].calls == [] and recorded["process"].calls == []
    source.refresh_from_db()
    assert source.status == "running" and source.run_id == held


def test_a_run_that_lost_its_source_never_deletes(
    source: Source, recorded: dict[str, Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`recover` released the slow run and a new run took the source: the old run's view of
    what is missing is stale, and it must not delete what the new run sees."""

    _docs(source, ["u:a", "u:b"])
    connector = Connector(["u:a"])
    newer: dict[str, str | None] = {}

    def released_and_restarted() -> None:
        Source.objects.filter(pk=source.pk).update(status=Source.Status.FAILED)  # recover
        newer["run"] = runs.claim_run(source.pk)  # the next run

    connector.during = released_and_restarted
    _use(monkeypatch, connector)
    tasks.discover_source(source.pk)
    assert recorded["delete"].calls == []
    source.refresh_from_db()
    assert source.status == "running" and source.run_id == newer["run"]  # left to its holder


def test_a_delete_queued_by_an_older_run_spares_what_a_newer_run_saw(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    _docs(source, ["u:a"])
    doc_id = document_id("dd", "u:a")
    started = timezone.now()
    Document.objects.filter(pk=doc_id).update(seen_at=started + timedelta(seconds=5))
    monkeypatch.setattr(tasks, "solr_client", lambda: pytest.fail("Solr must not be called"))
    assert tasks.delete_document(doc_id, seen_before=started.isoformat()) == "seen again"
    assert _status(source, "u:a") == "indexed"


@pytest.fixture
def editor(db: None, source: Source) -> dict[str, str]:
    user = User.objects.create_user("ed", password="x" * 16, is_staff=True)
    return {"Authorization": f"Bearer {issue_token(user, 't')}"}


def test_the_api_refuses_to_start_a_source_that_is_running(
    source: Source, editor: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Result:
        id = "task-1"

    started = Calls()

    def delay(*args: Any, **kwargs: Any) -> Result:
        started(*args, **kwargs)
        return Result()

    monkeypatch.setattr(tasks.discover_source, "delay", delay)
    client = Client()
    assert client.post(f"/api/sources/{source.pk}/run", headers=editor).status_code == 202
    runs.claim_run(source.pk)
    r = client.post(f"/api/sources/{source.pk}/run", headers=editor)
    assert r.status_code == 409 and "running" in r.json()["detail"]
    assert len(started.calls) == 1


# --- the reach of a delete (SEMA-011) ----------------------------------------------------------


class FakeSolr:
    def __init__(self) -> None:
        self.by_id: list[list[str]] = []
        self.by_query: list[str] = []

    def delete_by_id(self, collection: str, ids: list[str]) -> None:
        self.by_id.append(list(ids))

    def delete_by_query(self, collection: str, query: str) -> None:
        self.by_query.append(query)


class FakeGraph:
    def __init__(self, mentioned: list[str] | None = None) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.mentioned = mentioned or []

    def run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.statements.append((cypher, params))
        return [{"keys": self.mentioned}]

    def execute(self, cypher: str, **params: Any) -> None:
        self.statements.append((cypher, params))


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeSolr, FakeGraph]:
    from oss_server import services

    solr, graph = FakeSolr(), FakeGraph(mentioned=["person:x"])
    monkeypatch.setattr(tasks, "solr_client", lambda: solr)
    monkeypatch.setattr(services, "solr_client", lambda: solr)
    monkeypatch.setattr(services, "graph_client", lambda: graph)
    return solr, graph


def _blob(content_hash: str, suffix: str = ".pdf") -> tuple[Path, Path]:
    root = retention.blob_root()
    blob = root / content_hash[:2] / f"{content_hash}{suffix}"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"original")
    previews = root / "previews" / content_hash
    previews.mkdir(parents=True, exist_ok=True)
    (previews / "1.webp").write_bytes(b"page")
    return blob, previews


def test_deleting_a_document_removes_its_files_and_keeps_only_a_tombstone(
    source: Source, stores: tuple[FakeSolr, FakeGraph]
) -> None:
    solr, graph = stores
    digest = "a" * 64
    blob, previews = _blob(digest)
    work = work_dir(SourceConfig(id=str(source.pk), collection="dd", connector="x"), "web")
    leftover = work / f"{digest}.html"
    leftover.write_bytes(b"downloaded")
    doc = Document.objects.create(
        id="d" * 32,
        collection=source.collection,
        source=source,
        uri="file:///import/person.pdf",
        content_hash=digest,
        status=Document.Status.INDEXED,
        title="Medical record of a person",
        text="private text",
        tags=[{"iri": "x"}],
        metadata={"tika": {"dc:creator": "a person"}},
        fields={"curated": {"x": {"decision": "accept"}}, "x_owner_s": "a person"},
        blob_path=str(blob),
        item={"uri": "u", "metadata": {"path": str(leftover)}},
    )
    assert tasks.delete_document(doc.id) == "deleted"
    doc.refresh_from_db()
    assert doc.status == "deleted" and doc.title == "" and doc.text == "" and doc.tags == []
    assert doc.metadata == {} and doc.blob_path == "" and doc.item == {}
    assert doc.fields == {"curated": {"x": {"decision": "accept"}}}  # for a revival
    assert not blob.exists() and not previews.exists() and not leftover.exists()
    assert solr.by_id == [[doc.id]]
    cypher = " ".join(c for c, _ in graph.statements)
    assert "REMOVE d.title, d.source_uri" in cypher  # a node kept for curator decisions
    assert ("UNWIND $keys AS k" in cypher) and graph.statements[-1][1] == {"keys": ["person:x"]}


def test_a_blob_another_document_uses_stays(
    source: Source, stores: tuple[FakeSolr, FakeGraph]
) -> None:
    digest = "b" * 64
    blob, previews = _blob(digest)
    other = Collection.objects.create(slug="other", name="other")
    other_source = Source.objects.create(collection=other, name="s", connector="fake")
    for n, (coll, src) in enumerate(((source.collection, source), (other, other_source))):
        Document.objects.create(
            id=str(n) * 32,
            collection=coll,
            source=src,
            uri="u",
            content_hash=digest,
            status=Document.Status.INDEXED,
            blob_path=str(blob),
        )
    tasks.delete_document("0" * 32)
    assert blob.exists() and previews.exists()  # the other collection's copy of the file


def test_deleting_a_source_reaches_the_graph_files_and_work_area(
    source: Source,
    editor: dict[str, str],
    stores: tuple[FakeSolr, FakeGraph],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solr, graph = stores
    digest = "c" * 64
    blob, previews = _blob(digest)
    work = work_dir(SourceConfig(id=str(source.pk), collection="dd", connector="x"), "web")
    (work / "page.html").write_bytes(b"x")
    Document.objects.create(
        id="e" * 32,
        collection=source.collection,
        source=source,
        uri="u",
        content_hash=digest,
        status=Document.Status.INDEXED,
        blob_path=str(blob),
    )
    purged, areas = Calls(), Calls()
    monkeypatch.setattr(tasks.purge_documents, "delay", purged)
    monkeypatch.setattr(tasks.purge_area, "delay", areas)
    r = Client().delete(f"/api/sources/{source.pk}", headers=editor)
    assert r.status_code == 204
    assert not Source.objects.filter(pk=source.pk).exists()
    assert any("source_id_s" in q for q in solr.by_query)  # out of search at once
    assert any("e" * 32 in q for q in solr.by_query)  # its passages too
    # the queued work, run here as the worker would
    ((ids, files), _), *_ = purged.calls
    tasks.purge_documents(ids, files)
    tasks.purge_area(*areas.calls[0][0], **areas.calls[0][1])
    assert ids == ["e" * 32] and not blob.exists() and not previews.exists()
    assert not work.exists()
    (cypher, params), *_ = graph.statements
    assert "DETACH DELETE d" in cypher and params == {"ids": ["e" * 32]}


def test_deleting_a_collection_removes_its_uploads(
    source: Source, editor: dict[str, str], stores: tuple[FakeSolr, FakeGraph]
) -> None:
    from oss_pipeline.connectors.upload import store_upload

    upload = store_upload(retention.data_dir(), "dd", "notes.txt", b"private notes")
    retention.remove_area("dd", None, uploads=True)
    assert not upload.exists() and not upload.parent.parent.exists()


def test_the_sweep_removes_what_no_document_uses_and_spares_young_files(
    source: Source,
) -> None:
    used, orphan, young = "1" * 64, "2" * 64, "3" * 64
    for digest in (used, orphan, young):
        _blob(digest)
    _docs(source, ["u:used"])
    Document.objects.filter(source=source).update(content_hash=used)
    old = time.time() - retention.GC_GRACE_S - 60
    root = retention.blob_root()
    for digest in (used, orphan):
        for path in (root / digest[:2] / f"{digest}.pdf", root / "previews" / digest):
            os.utime(path, (old, old))
    work = work_dir(SourceConfig(id="9", collection="dd", connector="x"), "web")
    stale, fresh = work / "stale.html", work / "fresh.html"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")
    os.utime(stale, (old - retention.WORK_MAX_AGE_S, old - retention.WORK_MAX_AGE_S))
    out = retention.gc(now=time.time())
    assert out == {"blobs": 1, "previews": 1, "work": 1}
    assert (root / used[:2] / f"{used}.pdf").exists() and (root / "previews" / used).exists()
    assert not (root / orphan[:2] / f"{orphan}.pdf").exists()
    assert (root / young[:2] / f"{young}.pdf").exists()  # may belong to a run in progress
    assert fresh.exists() and not stale.exists()


# --- the document task (SEMA-025, NEW-6, SEMA-060, SEMA-061) ---------------------------------


def test_a_document_of_a_removed_connector_fails_instead_of_hanging(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    _docs(source, ["u:a"], status=Document.Status.QUEUED)
    monkeypatch.setattr(tasks, "connectors", dict)
    doc_id = document_id("dd", "u:a")
    assert tasks.process_document(doc_id, {"uri": "u:a", "content_hash": "h-u:a"}) == "failed"
    doc = Document.objects.get(pk=doc_id)
    assert doc.status == "failed" and "unknown connector fake" in doc.error
    assert doc.processing_since is None and doc.attempts == 0


def test_a_failure_outside_the_stages_is_reported_on_the_document(
    source: Source, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _docs(source, ["u:a"], status=Document.Status.QUEUED)
    file = tmp_path / "a.txt"
    file.write_text("x")

    class Fetching:
        def fetch(self, item: SourceItem) -> Any:
            from oss_core.contracts import FetchedItem

            return FetchedItem(item=item, path=str(file))

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("database went away")

    monkeypatch.setattr(tasks, "connectors", lambda: {"fake": Fetching()})
    monkeypatch.setattr(tasks, "_run", broken)
    doc_id = document_id("dd", "u:a")
    assert tasks.process_document(doc_id, {"uri": "u:a", "content_hash": "h-u:a"}) == "failed"
    doc = Document.objects.get(pk=doc_id)
    assert doc.status == "failed" and "database went away" in doc.error
    assert doc.processing_since is None  # not left `processing` for recover to retry


def test_the_work_file_goes_once_the_original_is_stored(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_core.contracts import FetchedItem

    _docs(source, ["u:a"], status=Document.Status.QUEUED)
    config = SourceConfig(id=str(source.pk), collection="dd", connector="fake")
    downloaded = work_dir(config, "web") / "page.html"
    downloaded.write_text("<p>page</p>")

    class Fetching:
        def fetch(self, item: SourceItem) -> FetchedItem:
            return FetchedItem(item=item, path=str(item.metadata["path"]))

    def run(stages: Any, work: WorkingDocument, ctx: StageContext, **kw: Any) -> WorkingDocument:
        work.fields["blob_path"] = "/data/blobs/x"
        work.fields["indexed"] = True
        return work

    monkeypatch.setattr(tasks, "connectors", lambda: {"fake": Fetching()})
    monkeypatch.setattr(tasks, "_run", run)
    monkeypatch.setattr(tasks, "profile", lambda name: [])
    item = {"uri": "u:a", "content_hash": "h-u:a", "metadata": {"path": str(downloaded)}}
    assert tasks.process_document(document_id("dd", "u:a"), item) == "indexed"
    assert not downloaded.exists()


def test_discovery_removes_work_files_nothing_will_read(
    source: Source, recorded: dict[str, Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = SourceConfig(id=str(source.pk), collection="dd", connector="fake")
    folder = work_dir(config, "web")
    _docs(source, ["u:indexed"])
    _docs(source, ["u:queued"], status=Document.Status.QUEUED)

    class Writing(Connector):
        def discover(self, config: Any, since: Any) -> Iterator[SourceItem]:
            self.report = RunReport()
            for uri in self.uris:
                path = folder / f"{uri[2:]}.html"
                path.write_text(uri)
                yield SourceItem(uri=uri, content_hash=f"h-{uri}", metadata={"path": str(path)})

    _use(monkeypatch, Writing(["u:indexed", "u:queued"]))
    tasks.discover_source(source.pk)
    assert not (folder / "indexed.html").exists()  # unchanged: nothing reads it
    assert (folder / "queued.html").exists()  # its waiting task reads it


def test_a_file_changed_after_discovery_is_indexed_under_what_was_read(
    source: Source,
) -> None:
    _docs(source, ["u:a"], status=Document.Status.PROCESSING)
    row = Document.objects.get(pk=document_id("dd", "u:a"))
    work = WorkingDocument(
        id=row.id, collection="dd", source_id=str(source.pk), uri="u:a", content_hash="new"
    )
    work.fields["indexed"] = True
    tasks._store(row, work)
    row.refresh_from_db()
    assert row.content_hash == "new"
