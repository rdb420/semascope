"""OCR later: the first pass indexes the text layer, a low-priority task OCRs it afterwards."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oss_core.contracts import FetchedItem, SourceItem, StageContext, WorkingDocument
from oss_pipeline.clients.tika import Extraction, TikaError
from oss_pipeline.stages.extract import ExtractStage
from oss_pipeline.stages.fetch import FetchStage
from oss_pipeline.stages.index import to_solr_document
from oss_server.catalogue.models import Collection, Document, Source, document_id
from oss_server.pipeline import ocr, tasks
from oss_server.pipeline.overrides import refused_overrides


class FakeTika:
    def __init__(self, fail_ocr: bool = False) -> None:
        self.calls: list[bool] = []
        self.fail_ocr = fail_ocr

    def extract(self, path: Path, *, ocr: bool = True, ocr_languages: str | None = None) -> Any:
        self.calls.append(ocr)
        if ocr and self.fail_ocr:
            raise TikaError("Tika 500 on scan.pdf: Tesseract crashed", 500)
        return Extraction(
            text="Kookaburra survey, scanned page" if ocr else "",
            metadata={},
            media_type="application/pdf",
            language="en",
            page_count=1,
            ocr_used=ocr,
        )

    def language(self, text: str) -> str | None:
        return "en"


class FakeIndex:
    name, version, requires, provides = "index", "2", ("text",), ("indexed",)

    def __init__(self) -> None:
        self.solr: list[dict[str, Any]] = []

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        self.solr.append(to_solr_document(doc))
        doc.fields["indexed"] = True
        return doc


class Connector:
    def __init__(self, path: Path) -> None:
        self.path = path

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(item=item, path=str(self.path))


class Calls:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))


@pytest.fixture
def setup(db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    c = Collection.objects.create(slug="ocr", name="ocr", settings={"pipeline": {"ocr": "later"}})
    s = Source.objects.create(collection=c, name="s", connector="filesystem")
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(b"%PDF-1.7 a scanned page")
    tika, index = FakeTika(), FakeIndex()
    stages = [FetchStage(), ExtractStage(tika=tika), index]  # type: ignore[arg-type]
    monkeypatch.setattr(tasks, "profile", lambda name: stages)
    monkeypatch.setattr(ocr, "profile", lambda name: stages)
    monkeypatch.setattr(tasks, "connectors", lambda: {"filesystem": Connector(scan)})
    queued = Calls()
    monkeypatch.setattr(ocr.ocr_document, "apply_async", queued)
    uri = "file:///import/scan.pdf"
    doc = Document.objects.create(
        id=document_id("ocr", uri), collection=c, source=s, uri=uri, content_hash=""
    )
    return {"doc": doc, "tika": tika, "index": index, "queued": queued, "uri": uri}


def _process(setup: dict[str, Any]) -> Document:
    item = {"uri": setup["uri"], "content_hash": "", "metadata": {"path": "/x"}}
    tasks.process_document.push_request(retries=0)
    try:
        assert tasks.process_document(setup["doc"].id, item) == "indexed"
    finally:
        tasks.process_document.pop_request()
    row: Document = Document.objects.get(pk=setup["doc"].id)
    return row


def _ocr(row: Document) -> str:
    ocr.ocr_document.push_request(retries=0)
    try:
        return str(ocr.ocr_document(row.id, row.content_hash))
    finally:
        ocr.ocr_document.pop_request()


def test_the_first_pass_indexes_without_ocr_and_queues_the_ocr_pass(setup: dict[str, Any]) -> None:
    row = _process(setup)
    assert setup["tika"].calls == [False]
    assert row.status == "indexed" and row.metadata["ocr_pending"] is True
    assert setup["index"].solr[-1]["ocr_pending_b"] is True
    assert ocr.pending().count() == 1
    (args, kwargs) = setup["queued"].calls[0]
    assert args == ((row.id, row.content_hash),) and kwargs == {"priority": ocr.OCR_PRIORITY}


def test_the_ocr_pass_replays_extract_with_ocr_and_keeps_the_fingerprint(
    setup: dict[str, Any],
) -> None:
    row = _process(setup)
    fp = row.fingerprint
    assert _ocr(row) == "indexed"
    row.refresh_from_db()
    assert setup["tika"].calls == [False, True]
    assert "Kookaburra" in row.text and row.metadata["ocr"] is True
    assert "ocr_pending" not in row.metadata and "x_ocr_pending_b" not in row.fields
    assert "ocr_pending_b" not in setup["index"].solr[-1]
    assert row.fingerprint == fp  # a later source run finds it done
    assert ocr.pending().count() == 0
    assert _ocr(row) == "skipped"  # a duplicate task does nothing
    assert setup["tika"].calls == [False, True]


def test_a_changed_document_is_not_ocred_by_an_old_task(setup: dict[str, Any]) -> None:
    row = _process(setup)
    old = row.content_hash
    Document.objects.filter(pk=row.id).update(content_hash="newer")
    ocr.ocr_document.push_request(retries=0)
    try:
        assert ocr.ocr_document(row.id, old) == "skipped"
    finally:
        ocr.ocr_document.pop_request()


def test_a_failed_ocr_pass_keeps_the_text_layer_searchable(setup: dict[str, Any]) -> None:
    row = _process(setup)
    setup["tika"].fail_ocr = True
    assert _ocr(row) == "ocr failed"
    row.refresh_from_db()
    assert row.status == "indexed" and "ocr_pending" not in row.metadata
    assert "Tesseract" in row.metadata["ocr_error"] and row.error.startswith("ocr: ")
    assert "ocr_pending_b" not in setup["index"].solr[-1]


def test_a_full_reindex_queues_a_lost_ocr_pass_again(
    setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _process(setup)
    setup["queued"].calls.clear()
    tasks.reindex_document.push_request(retries=0)
    try:
        tasks.reindex_document(row.id)
    finally:
        tasks.reindex_document.pop_request()
    assert setup["queued"].calls and setup["queued"].calls[0][0] == ((row.id, row.content_hash),)


def test_stats_count_pending_documents(setup: dict[str, Any], client: Any) -> None:
    from django.contrib.auth.models import User

    _process(setup)
    admin = User.objects.create_superuser("root", "r@example.test", "pw-for-tests-only")
    client.force_login(admin)
    assert client.get("/api/stats").json()["ocr_pending"] == 1


def test_a_collection_may_set_ocr_to_now_later_or_off_only() -> None:
    for value in ("now", "later", "off", True, False):
        assert refused_overrides({"pipeline": {"ocr": value}}) == []
    assert refused_overrides({"pipeline": {"ocr": "sometimes"}}) == ["ocr"]
    assert refused_overrides({"pipeline": {"ocr_min_chars_per_page": 10}}) == []
