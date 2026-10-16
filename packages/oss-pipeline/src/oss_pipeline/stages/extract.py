"""extract: text and metadata through Tika, with OCR when the format needs it.

With `extract_provider: mineru`, PDF, Office (Word, PowerPoint and Excel in
their OOXML formats) and raster image files are parsed by a MinerU server
(layout-aware Markdown, GPU OCR) and Tika only reads their metadata; every
other format still goes through Tika. The choice is by media type
(`mineru_handles`), not by content-type group, which stays the coarse facet.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.clients.mineru import MineruClient, MineruError
from oss_pipeline.clients.tika import TikaClient
from oss_pipeline.media import content_type_group

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
                return None
            self._mineru = MineruClient(str(base), str(ctx.settings.get("extract_api_key") or ""))
        return self._mineru

    def _client(self, ctx: StageContext) -> TikaClient:
        if self._tika is None:
            self._tika = TikaClient(str(ctx.settings["tika_url"]))
        return self._tika

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        path = Path(str(doc.fields["blob_path"]))
        ocr = bool(ctx.settings.get("ocr", True))
        mineru = self._mineru_client(ctx)
        # a MinerU server does the heavy parsing; Tika then only reads metadata (no OCR)
        probe = self._client(ctx).extract(path, ocr=ocr and mineru is None)
        media_type = probe.media_type or doc.media_type or ""
        result = probe
        extractor = "tika"
        if mineru is not None and mineru_handles(media_type):
            try:
                markdown = mineru.parse(path, media_type, ocr_mode="auto" if ocr else "txt")
            except (MineruError, OSError) as exc:
                raise RuntimeError(f"mineru failed: {exc}") from exc
            result = replace(probe, text=markdown, ocr_used=ocr)
            extractor = "mineru"
        doc.text = result.text
        doc.media_type = result.media_type or doc.media_type
        language = (result.language or doc.language or "").lower()[:2] or None
        if language is None and result.text and bool(ctx.settings.get("detect_language", True)):
            language = self._client(ctx).language(result.text)
        doc.language = language
        title = next((str(result.metadata[k]) for k in _TITLE_KEYS if result.metadata.get(k)), None)
        if isinstance(title, list):
            title = title[0]
        doc.title = doc.title or title or Path(doc.uri).name
        doc.metadata.update(
            {
                "page_count": result.page_count,
                "ocr": result.ocr_used,
                "content_type_group": content_type_group(doc.media_type),
                "embedded_count": len(result.embedded),
                "extractor": extractor,
                "tika": {
                    k: v
                    for k, v in result.metadata.items()
                    if k.startswith(("dc:", "dcterms:", "pdf:", "meta:", "xmp"))
                },
            }
        )
        return doc
