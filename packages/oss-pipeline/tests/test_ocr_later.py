"""OCR now, later or off: what the extract stage reads, and what it marks as pending."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.clients.tika import Extraction
from oss_pipeline.ocr_later import needs_ocr, ocr_mode, valid_ocr_setting
from oss_pipeline.stages.extract import ExtractStage
from oss_pipeline.stages.index import to_solr_document


class FakeTika:
    """Tika as it answers for a scanned PDF: text only when OCR is on."""

    def __init__(self, media_type: str, layer: str = "", embedded: list[str] | None = None):
        self.media_type = media_type
        self.layer = layer
        self.embedded = embedded or []
        self.ocr: list[bool] = []

    def extract(self, path: Path, *, ocr: bool = True, ocr_languages: str | None = None) -> Any:
        self.ocr.append(ocr)
        text = (self.layer + " " if self.layer else "") + ("Recognised words." if ocr else "")
        return Extraction(
            text=text.strip(),
            metadata={},
            media_type=self.media_type,
            language="en",
            page_count=2,
            embedded=[{"Content-Type": mt} for mt in self.embedded],
            ocr_used=ocr,
        )

    def language(self, text: str) -> str | None:
        return "en"


def _run(tmp_path: Path, tika: FakeTika, ocr: object) -> WorkingDocument:
    blob = tmp_path / "scan.pdf"
    blob.write_bytes(b"%PDF-1.7 not really")
    doc = WorkingDocument(
        id="d", collection="c", source_id="1", uri="file:///scan.pdf", content_hash="h"
    )
    doc.fields["blob_path"] = str(blob)
    ctx = StageContext(collection="c", profile="fast", settings={"ocr": ocr, "tika_url": "x"})
    return ExtractStage(tika=tika).run(doc, ctx)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "mode"),
    [(True, "now"), (False, "off"), ("later", "later"), (" LATER ", "later"), ("off", "off")],
)
def test_the_ocr_setting_reads_booleans_as_before(value: object, mode: str) -> None:
    assert ocr_mode({"ocr": value}) == mode
    assert valid_ocr_setting(value)


def test_unknown_ocr_values_are_refused_and_default_to_now() -> None:
    assert ocr_mode({}) == "now"
    assert ocr_mode({"ocr": "sometimes"}) == "now"
    assert not valid_ocr_setting("sometimes")
    assert not valid_ocr_setting(1)


@pytest.mark.parametrize(
    ("media_type", "text", "pages", "embedded", "expected"),
    [
        ("image/png", "", 1, [], True),
        ("image/ocr-jpeg", "", 1, [], True),
        ("image/svg+xml", "", 1, [], False),
        ("application/pdf", "", 3, [], True),
        ("application/pdf", "x" * 39, 1, [], True),
        ("application/pdf", "x" * 400, 2, [], False),
        ("text/plain", "", 1, [], False),
        ("application/msword", "words", 1, ["image/png"], True),
        ("message/rfc822", "words", 1, ["application/pdf"], False),
    ],
)
def test_what_needs_ocr(
    media_type: str, text: str, pages: int, embedded: list[str], expected: bool
) -> None:
    assert needs_ocr(media_type, text, pages, embedded) is expected


def test_the_threshold_per_page_is_a_collection_setting() -> None:
    assert not needs_ocr("application/pdf", "x" * 30, 1, settings={"ocr_min_chars_per_page": 10})


def test_later_reads_the_text_layer_only_and_marks_the_document(tmp_path: Path) -> None:
    tika = FakeTika("application/pdf", layer="Page header")
    doc = _run(tmp_path, tika, "later")
    assert tika.ocr == [False]  # no Tesseract in the first pass
    assert doc.text == "Page header"
    assert doc.metadata["ocr_pending"] is True and doc.metadata["ocr"] is False
    solr = to_solr_document(doc)
    assert solr["ocr_pending_b"] is True and solr["ocr_b"] is False


def test_now_ocrs_in_the_same_run_and_clears_a_pending_mark(tmp_path: Path) -> None:
    tika = FakeTika("application/pdf")
    doc = _run(tmp_path, tika, "now")
    assert tika.ocr == [True] and doc.text == "Recognised words."
    assert "ocr_pending" not in doc.metadata and doc.metadata["ocr"] is True
    assert "ocr_pending_b" not in to_solr_document(doc)
    # the second pass of `later` runs this stage again on a document marked pending
    doc.metadata["ocr_pending"], doc.fields["x_ocr_pending_b"] = True, True
    tika2 = FakeTika("application/pdf")
    again = ExtractStage(tika=tika2).run(  # type: ignore[arg-type]
        doc, StageContext(collection="c", profile="fast", settings={"ocr": "now"})
    )
    assert "ocr_pending" not in again.metadata and "x_ocr_pending_b" not in again.fields


def test_later_does_not_mark_a_document_with_a_text_layer(tmp_path: Path) -> None:
    tika = FakeTika("application/pdf", layer="A full text layer. " * 20)
    doc = _run(tmp_path, tika, "later")
    assert tika.ocr == [False] and "ocr_pending" not in doc.metadata


def test_off_never_ocrs_and_never_marks(tmp_path: Path) -> None:
    for value in ("off", False):
        tika = FakeTika("image/png")
        doc = _run(tmp_path, tika, value)
        assert tika.ocr == [False] and "ocr_pending" not in doc.metadata
