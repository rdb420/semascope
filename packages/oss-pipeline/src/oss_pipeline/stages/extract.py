"""extract: text and metadata through Tika, with OCR when the format needs it."""

from __future__ import annotations

from pathlib import Path

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.clients.tika import TikaClient
from oss_pipeline.media import content_type_group

_TITLE_KEYS = ("dc:title", "title", "pdf:docinfo:title", "og:title")


class ExtractStage:
    name = "extract"
    version = "1"
    queue = "cpu"
    requires = ("blob_path",)
    provides = ("text", "media_type", "language", "title", "metadata")

    def __init__(self, tika: TikaClient | None = None) -> None:
        self._tika = tika

    def _client(self, ctx: StageContext) -> TikaClient:
        if self._tika is None:
            self._tika = TikaClient(str(ctx.settings["tika_url"]))
        return self._tika

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        path = Path(str(doc.fields["blob_path"]))
        ocr = bool(ctx.settings.get("ocr", True))
        result = self._client(ctx).extract(path, ocr=ocr)
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
                "tika": {
                    k: v
                    for k, v in result.metadata.items()
                    if k.startswith(("dc:", "dcterms:", "pdf:", "meta:", "xmp"))
                },
            }
        )
        return doc
