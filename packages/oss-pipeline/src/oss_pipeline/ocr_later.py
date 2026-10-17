"""OCR now, later or off (docs/adr/0014-ocr-later.md).

A collection's `settings.pipeline.ocr` says when scanned content is OCRed:

- `now` (the default, also `true`): in the document's own run, as before;
- `later`: the document's run reads only the text layer and metadata (no Tesseract, MinerU in
  `txt` mode), so it is searchable at once; when the result looks like it needs OCR, the
  document is marked `ocr_pending` and a low-priority task OCRs it afterwards and runs the
  stages after `extract` again (`oss_server.pipeline.ocr`);
- `off` (also `false`): never.

Whether a document needs OCR is judged from what the first pass found: a raster image, a PDF
with fewer than `ocr_min_chars_per_page` (40) characters of text per page, or a container
(a Word file, an e-mail) holding raster images.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from oss_core.contracts import WorkingDocument

OCR_MODES = ("now", "later", "off")
MIN_CHARS_PER_PAGE = 40
PENDING = "ocr_pending"  # doc.metadata key
PENDING_FIELD = "x_ocr_pending_b"  # indexed as `ocr_pending_b`: a filter for what waits
VECTOR_IMAGES = frozenset({"image/svg+xml"})


def ocr_mode(settings: Mapping[str, Any]) -> str:
    """`now`, `later` or `off` from a stage setting (booleans as before: true now, false off)."""

    value = settings.get("ocr", True)
    if isinstance(value, bool):
        return "now" if value else "off"
    mode = str(value).strip().lower()
    return mode if mode in OCR_MODES else "now"


def valid_ocr_setting(value: object) -> bool:
    """Whether a collection may set `ocr` to this value."""

    return isinstance(value, bool) or (
        isinstance(value, str) and value.strip().lower() in OCR_MODES
    )


def _media(value: object) -> str:
    return str(value or "").split(";")[0].strip().lower()


def is_raster(media_type: object) -> bool:
    mt = _media(media_type)
    if mt.startswith("image/ocr-"):  # Tika's marker for an image it OCRed
        mt = "image/" + mt[len("image/ocr-") :]
    return mt.startswith("image/") and mt not in VECTOR_IMAGES


def needs_ocr(
    media_type: str | None,
    text: str | None,
    page_count: int | None,
    embedded_media_types: Iterable[object] = (),
    settings: Mapping[str, Any] | None = None,
) -> bool:
    """Whether a text-layer-only extraction left content that only OCR can read."""

    if is_raster(media_type):
        return True
    if _media(media_type) == "application/pdf":
        try:
            per_page = int((settings or {}).get("ocr_min_chars_per_page", MIN_CHARS_PER_PAGE))
        except (TypeError, ValueError):
            per_page = MIN_CHARS_PER_PAGE
        pages = max(int(page_count or 1), 1)
        return len((text or "").strip()) < per_page * pages
    return any(is_raster(mt) for mt in embedded_media_types)


def mark_pending(doc: WorkingDocument, pending: bool) -> None:
    """Record on the document (Postgres and the index) whether its OCR is still to come."""

    if pending:
        doc.metadata[PENDING] = True
        doc.fields[PENDING_FIELD] = True
    else:
        doc.metadata.pop(PENDING, None)
        doc.fields.pop(PENDING_FIELD, None)
