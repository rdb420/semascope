"""MinerU is chosen by media type; the content-type group keeps its facet meaning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.clients.tika import Extraction
from oss_pipeline.media import content_type_group
from oss_pipeline.stages.extract import ExtractStage, mineru_handles

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class FakeTika:
    def __init__(self, media_type: str) -> None:
        self.media_type = media_type
        self.ocr: list[bool] = []

    def extract(self, path: Path, *, ocr: bool = True) -> Extraction:
        self.ocr.append(ocr)
        return Extraction(
            text="tika text", metadata={}, media_type=self.media_type, language="en", page_count=1
        )

    def language(self, text: str) -> str | None:
        return "en"


class FakeMineru:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def parse(self, path: Path, media_type: str, *, ocr_mode: str = "auto") -> str:
        self.calls.append(media_type)
        return "# mineru markdown"


@pytest.mark.parametrize(
    ("media_type", "handled"),
    [
        ("application/pdf", True),
        (DOCX, True),
        (PPTX, True),
        (XLSX, True),
        ("image/png", True),
        ("image/jpeg; charset=binary", True),
        ("image/svg+xml", False),
        ("text/plain", False),
        ("text/html", False),
        ("application/msword", False),
        (None, False),
    ],
)
def test_mineru_handles_pdf_office_and_raster_images(media_type: str, handled: bool) -> None:
    assert mineru_handles(media_type) is handled


def test_content_type_group_is_unchanged() -> None:
    # the facet still files a PDF under text and an XLSX under spreadsheet
    assert content_type_group("application/pdf") == "text"
    assert content_type_group(XLSX) == "spreadsheet"
    assert content_type_group(PPTX) == "presentation"


@pytest.mark.parametrize(
    ("media_type", "extractor"),
    [
        ("application/pdf", "mineru"),
        (DOCX, "mineru"),
        ("image/png", "mineru"),
        ("text/csv", "tika"),
    ],
)
def test_extract_sends_pdf_and_office_to_mineru(
    tmp_path: Path, media_type: str, extractor: str
) -> None:
    blob = tmp_path / "blob"
    blob.write_bytes(b"x")
    tika, mineru = FakeTika(media_type), FakeMineru()
    stage = ExtractStage(tika=tika, mineru=mineru)  # type: ignore[arg-type]
    settings: dict[str, Any] = {"extract_provider": "mineru", "tika_url": "http://tika"}
    doc = WorkingDocument(
        id="d",
        collection="c",
        source_id="1",
        uri="file:///x",
        content_hash="h",
        fields={"blob_path": str(blob)},
    )
    out = stage.run(doc, StageContext(collection="c", profile="standard", settings=settings))
    assert out.metadata["extractor"] == extractor
    assert (out.text == "# mineru markdown") is (extractor == "mineru")
    assert mineru.calls == ([media_type] if extractor == "mineru" else [])
    assert tika.ocr == [False], "with MinerU configured Tika only reads metadata"
