"""extract: text and metadata through Tika, with OCR when the format needs it.

With `extract_provider: mineru`, PDF, Office (Word, PowerPoint and Excel in
their OOXML formats) and raster image files are parsed by a MinerU server
(layout-aware Markdown, GPU OCR) and Tika only reads their metadata; every
other format still goes through Tika, with Tika's OCR as without MinerU. The
choice is by media type (`mineru_handles`), not by content-type group, which
stays the coarse facet. `extract_provider: mineru` without `extract_base_url`
is a configuration error, never a silent fall-back to Tika.

`ocr_languages` (a collection's `settings.pipeline`, e.g. `eng+deu`) sets the
Tesseract languages Tika OCRs with; the default is the Tika configuration's
(`eng`). Text Tika finds in embedded items (an e-mail's attachments, the files
of a zip, the images of a Word file) is added after the document's own text,
headed by the item's name, at most `embedded_max_items` (100) items and
`embedded_max_chars` (1,000,000) characters; sources whose connector already
makes those items documents of their own (mail) skip it.

`ocr` is `now` (default), `later` or `off` (`oss_pipeline.ocr_later`): with `later` this
stage reads the text layer only and marks a document that needs OCR `ocr_pending`.
"""

from __future__ import annotations

import logging
import mimetypes
from dataclasses import replace
from pathlib import Path
from typing import Any

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.clients.mineru import MineruClient, MineruError
from oss_pipeline.clients.tika import Extraction, TikaClient, primary_language, valid_ocr_languages
from oss_pipeline.media import content_type_group
from oss_pipeline.ocr_later import mark_pending, needs_ocr, ocr_mode

log = logging.getLogger(__name__)

EMBEDDED_MAX_ITEMS = 100
EMBEDDED_MAX_CHARS = 1_000_000
_TITLE_KEYS = ("dc:title", "title", "pdf:docinfo:title", "og:title")
MINERU_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)
MINERU_IMAGE_EXCLUDED = frozenset({"image/svg+xml"})  # vector drawings: Tika reads their text


def mineru_handles(media_type: str | None) -> bool:
    """Whether a MinerU server parses this media type: PDF, OOXML Office and raster images."""

    mt = (media_type or "").split(";")[0].strip().lower()
    if mt in MINERU_MEDIA_TYPES:
        return True
    return mt.startswith("image/") and mt not in MINERU_IMAGE_EXCLUDED


class ExtractStage:
    name = "extract"
    version = "1"
    requires = ("blob_path",)
    provides = ("text", "media_type", "language", "title", "metadata")

    def __init__(self, tika: TikaClient | None = None, mineru: MineruClient | None = None) -> None:
        self._tika = tika
        self._mineru = mineru

    def _mineru_client(self, ctx: StageContext) -> MineruClient | None:
        if str(ctx.settings.get("extract_provider", "tika")) != "mineru":
            return None
        if self._mineru is None:
            base = ctx.settings.get("extract_base_url")
            if not base:
                raise ValueError(
                    "extract_provider is mineru but no extract_base_url is set "
                    "(SEMASCOPE_EXTRACT_BASE_URL)"
                )
            self._mineru = MineruClient(str(base), str(ctx.settings.get("extract_api_key") or ""))
        return self._mineru

    def _client(self, ctx: StageContext) -> TikaClient:
        if self._tika is None:
            self._tika = TikaClient(str(ctx.settings["tika_url"]))
        return self._tika

    def _tika_extract(self, ctx: StageContext, path: Path, *, ocr: bool) -> Extraction:
        languages = valid_ocr_languages(ctx.settings.get("ocr_languages"))
        if ctx.settings.get("ocr_languages") and languages is None:
            log.warning(
                "ocr_languages %r ignored: not Tesseract codes", ctx.settings["ocr_languages"]
            )
        if languages and ocr:
            return self._client(ctx).extract(path, ocr=True, ocr_languages=languages)
        return self._client(ctx).extract(path, ocr=ocr)

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        path = Path(str(doc.fields["blob_path"]))
        mode = ocr_mode(ctx.settings)  # now, later (a low-priority pass OCRs it) or off
        ocr = mode == "now"
        mineru = self._mineru_client(ctx)
        # MinerU parses what it handles and Tika then only reads metadata (no OCR); every
        # other format is Tika's, with OCR. The media type is guessed first, and Tika is
        # asked again with OCR when the guess sent a format MinerU does not take there.
        guess = doc.media_type or sniff(path) or mimetypes.guess_type(path.name)[0]
        for_mineru = mineru is not None and mineru_handles(guess)
        probe = self._tika_extract(ctx, path, ocr=ocr and not for_mineru)
        media_type = probe.media_type or doc.media_type or ""
        if for_mineru and ocr and not mineru_handles(media_type):
            probe = self._tika_extract(ctx, path, ocr=True)
        result = probe
        extractor = "tika"
        if mineru is not None and mineru_handles(media_type):
            try:
                markdown = mineru.parse(path, media_type, ocr_mode="auto" if ocr else "txt")
            except (MineruError, OSError) as exc:
                raise RuntimeError(f"mineru failed: {exc}") from exc
            result = replace(probe, text=markdown, ocr_used=ocr)
            extractor = "mineru"
        text, embedded = result.text, 0
        if not doc.fields.get("children_expanded"):
            text, embedded = with_embedded(text, result.embedded_text, ctx.settings)
        doc.text = text
        doc.media_type = result.media_type or doc.media_type
        language = primary_language(result.language) or primary_language(doc.language)
        if language is None and result.text and bool(ctx.settings.get("detect_language", True)):
            language = self._client(ctx).language(result.text)
        doc.language = language
        doc.title = doc.title or first_title(result.metadata) or Path(doc.uri).name
        doc.metadata.update(
            {
                "page_count": result.page_count,
                "ocr": result.ocr_used,
                "content_type_group": content_type_group(doc.media_type),
                "embedded_count": len(result.embedded),
                "embedded_indexed": embedded,
                "extractor": extractor,
                "tika": {
                    k: v
                    for k, v in result.metadata.items()
                    if k.startswith(("dc:", "dcterms:", "pdf:", "meta:", "xmp"))
                },
            }
        )
        embedded_types = [m.get("Content-Type") for m in result.embedded]
        later = mode == "later" and needs_ocr(
            doc.media_type, result.text, result.page_count, embedded_types, ctx.settings
        )
        mark_pending(doc, later)
        return doc


_MAGIC = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"BM", "image/bmp"),
)
_OOXML = {
    "word/": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "ppt/": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xl/": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def sniff(path: Path) -> str | None:
    """The media type of the formats MinerU takes, from the file's first bytes; else None."""

    try:
        with path.open("rb") as fh:
            head = fh.read(16)
    except OSError:
        return None
    for magic, media_type in _MAGIC:
        if head.startswith(magic):
            return media_type
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"PK\x03\x04":
        import zipfile

        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
        except (zipfile.BadZipFile, OSError):
            return None
        for prefix, media_type in _OOXML.items():
            if any(n.startswith(prefix) for n in names):
                return media_type
    return None


def first_title(metadata: dict[str, Any]) -> str | None:
    """The first non-empty title Tika reported; a multi-valued one gives its first value."""

    for key in _TITLE_KEYS:
        value = metadata.get(key)
        if isinstance(value, list | tuple):
            value = next((v for v in value if str(v).strip()), None)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def with_embedded(
    text: str, items: list[tuple[str, str, str]], settings: dict[str, Any]
) -> tuple[str, int]:
    """The text with the embedded items' text after it, within the limits; and how many."""

    max_items = int(settings.get("embedded_max_items", EMBEDDED_MAX_ITEMS))
    budget = int(settings.get("embedded_max_chars", EMBEDDED_MAX_CHARS))
    parts = [text] if text else []
    count = 0
    for name, _media_type, body in items[: max(0, max_items)]:
        if budget <= 0:
            break
        piece = f"{name}\n\n{body[:budget]}"
        budget -= len(body)
        parts.append(piece)
        count += 1
    return "\n\n".join(parts), count
