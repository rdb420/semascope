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
    ("media_type", "name", "extractor"),
    [
        ("application/pdf", "blob.pdf", "mineru"),
        (DOCX, "blob.docx", "mineru"),
        ("image/png", "blob.png", "mineru"),
        ("text/csv", "blob.csv", "tika"),
        ("application/msword", "blob.doc", "tika"),
    ],
)
def test_extract_sends_pdf_and_office_to_mineru(
    tmp_path: Path, media_type: str, name: str, extractor: str
) -> None:
    blob = tmp_path / name
    blob.write_bytes(b"x")
    tika, mineru = FakeTika(media_type), FakeMineru()
    out = _extract(tika, mineru, blob, {"extract_provider": "mineru"})
    assert out.metadata["extractor"] == extractor
    assert (out.text == "# mineru markdown") is (extractor == "mineru")
    assert mineru.calls == ([media_type] if extractor == "mineru" else [])
    # MinerU does the OCR of what it takes; Tika keeps OCR for everything else (SEMA-016)
    assert tika.ocr == [extractor == "tika"]


def _extract(
    tika: Any, mineru: Any, blob: Path, settings: dict[str, Any], **fields: Any
) -> WorkingDocument:
    stage = ExtractStage(tika=tika, mineru=mineru)
    doc = WorkingDocument(
        id="d",
        collection="c",
        source_id="1",
        uri="file:///x",
        content_hash="h",
        fields={"blob_path": str(blob), **fields},
    )
    ctx = StageContext(
        collection="c", profile="standard", settings={"tika_url": "http://tika", **settings}
    )
    return stage.run(doc, ctx)


def test_a_file_mineru_seemed_to_take_is_asked_again_with_ocr(tmp_path: Path) -> None:
    blob = tmp_path / "scan.pdf"  # named like a PDF, but Tika finds plain text
    blob.write_bytes(b"plain words")
    tika, mineru = FakeTika("text/plain"), FakeMineru()
    out = _extract(tika, mineru, blob, {"extract_provider": "mineru"})
    assert tika.ocr == [False, True] and mineru.calls == []
    assert out.metadata["extractor"] == "tika"


def test_the_first_bytes_decide_before_the_file_name(tmp_path: Path) -> None:
    blob = tmp_path / "blob"  # no suffix: a web page or an S3 object without one
    blob.write_bytes(b"%PDF-1.7 ...")
    tika, mineru = FakeTika("application/pdf"), FakeMineru()
    _extract(tika, mineru, blob, {"extract_provider": "mineru"})
    assert tika.ocr == [False] and mineru.calls == ["application/pdf"]


def test_mineru_without_an_address_is_a_configuration_error(tmp_path: Path) -> None:
    blob = tmp_path / "a.pdf"
    blob.write_bytes(b"%PDF-1.7")
    stage = ExtractStage(tika=FakeTika("application/pdf"))  # type: ignore[arg-type]
    doc = WorkingDocument(
        id="d",
        collection="c",
        source_id="1",
        uri="u",
        content_hash="h",
        fields={"blob_path": str(blob)},
    )
    ctx = StageContext(
        collection="c",
        profile="standard",
        settings={"tika_url": "http://tika", "extract_provider": "mineru"},
    )
    with pytest.raises(ValueError, match="extract_base_url"):
        stage.run(doc, ctx)


# --- OCR languages, embedded items, titles and languages -------------------------------------


def _tika(handler: Any) -> Any:
    import httpx

    from oss_pipeline.clients.tika import TikaClient

    client = TikaClient("http://tika")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_ocr_languages_reach_tika_and_only_as_tesseract_codes(tmp_path: Path) -> None:
    import httpx

    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, json=[{"X-TIKA:content": "text", "Content-Type": "image/png"}])

    blob = tmp_path / "scan.png"
    blob.write_bytes(b"\x89PNG\r\n\x1a\n")
    stage = ExtractStage(tika=_tika(handler))
    for value, header in (("eng+deu", "eng+deu"), (["fra", "chi_sim"], "fra+chi_sim")):
        _extract(stage._tika, None, blob, {"ocr_languages": value, "detect_language": False})
        assert seen[-1]["x-tika-ocrlanguage"] == header
    _extract(
        stage._tika, None, blob, {"ocr_languages": "eng\r\nX-Evil: 1", "detect_language": False}
    )
    assert "x-tika-ocrlanguage" not in seen[-1]  # never a header it was not meant to be


def test_embedded_items_are_searchable_with_their_container(tmp_path: Path) -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"X-TIKA:content": "the message", "Content-Type": "message/rfc822"},
                {
                    "X-TIKA:content": "minutes of the meeting",
                    "Content-Type": "application/pdf",
                    "resourceName": "minutes.pdf",
                },
                {"X-TIKA:content": "", "Content-Type": "image/png"},
                {"X-TIKA:content": "scanned receipt", "X-TIKA:embedded_resource_path": "/r.png"},
            ],
        )

    blob = tmp_path / "a.eml"
    blob.write_bytes(b"From: a\n\nx")
    settings = {"detect_language": False}
    out = _extract(_tika(handler), None, blob, settings)
    text = out.text or ""
    assert text.startswith("the message")
    assert "minutes.pdf\n\nminutes of the meeting" in text and "r.png\n\nscanned receipt" in text
    assert out.metadata["embedded_count"] == 3 and out.metadata["embedded_indexed"] == 2
    # a mail source makes attachments documents of their own: not repeated in the message
    alone = _extract(_tika(handler), None, blob, settings, children_expanded=True)
    assert alone.text == "the message"
    capped = _extract(_tika(handler), None, blob, {**settings, "embedded_max_items": 1}).text
    assert capped and "scanned receipt" not in capped and "minutes of the meeting" in capped


def test_a_multi_valued_title_and_a_regional_language(tmp_path: Path) -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "X-TIKA:content": "text",
                    "Content-Type": "application/pdf",
                    "dc:title": ["The Title", "Another"],
                    "dc:language": "zh-CN",
                }
            ],
        )

    blob = tmp_path / "a.pdf"
    blob.write_bytes(b"%PDF")
    out = _extract(_tika(handler), None, blob, {})
    assert out.title == "The Title" and out.language == "zh"


def test_tika_language_codes_keep_their_language() -> None:
    from oss_pipeline.clients.tika import primary_language

    assert primary_language("zh-cn") == "zh" and primary_language("pt_BR") == "pt"
    assert primary_language("en") == "en" and primary_language("und") is None
    assert primary_language("English") is None and primary_language("") is None
