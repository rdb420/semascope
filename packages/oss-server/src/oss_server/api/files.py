"""Original files: serve a document's stored original through the API (decision D-2).

The original is read from the blob store (`<data dir>/blobs`, where the fetch
stage keeps it under its content hash) and never by a path the client names.
The caller needs `reader` on the document's collection; the route lives in
`views.py`. Responses carry the stored media type, a Content-Disposition with
the original file name, `nosniff`, and honour a single byte range, so PDF
viewers and media players can seek.

Only types a browser renders without running the document's own code are
served inline (PDF, raster images, plain text, audio, video). Everything else,
HTML and SVG included, is an attachment, and a non-PDF response also carries a
sandboxing Content-Security-Policy.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import IO
from urllib.parse import unquote, urlparse

from django.http import FileResponse, HttpRequest, HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header
from ninja.errors import HttpError

from oss_core.config import get_settings
from oss_server.catalogue.models import Document

audit = logging.getLogger("oss_server.audit")

INLINE_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/avif",
        "image/bmp",
        "text/plain",
        "text/csv",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/x-wav",
        "audio/flac",
        "audio/mp4",
        "audio/webm",
        "video/mp4",
        "video/webm",
        "video/ogg",
    }
)
SANDBOX = (
    "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"
)
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$")
_CHUNK = 64 * 1024


def media_type(doc: Document) -> tuple[str, str]:
    """(base type, full Content-Type) of a document; octet-stream when unknown or malformed."""

    raw = (doc.media_type or "").strip()
    base, _, params = raw.partition(";")
    base = base.strip().lower()
    if not _MEDIA_TYPE.match(base):
        return "application/octet-stream", "application/octet-stream"
    charset = re.search(r"charset=([\w.:-]+)", params, re.I)
    full = base + (f"; charset={charset.group(1)}" if charset and base.startswith("text/") else "")
    return base, full


def original_name(doc: Document, blob: Path) -> str:
    """The file name to offer: the last segment of the URI, else the title, else the id."""

    path = urlparse(doc.uri).path if "://" in doc.uri else doc.uri
    name = unquote(PurePosixPath(path).name)
    if not name:
        name = (doc.title or "").strip()[:120] or f"document-{doc.id[:8]}"
    name = re.sub(r"[\x00-\x1f\x7f/\\]+", "_", name).strip(" .") or f"document-{doc.id[:8]}"
    if not PurePosixPath(name).suffix and blob.suffix:
        name += blob.suffix
    return name


def blob_root() -> Path:
    return (get_settings().semascope_data_dir / "blobs").resolve()


def stored_original(doc: Document) -> Path:
    """The stored original of a document, which must lie inside the blob store; 404 otherwise."""

    raw = doc.blob_path or str(doc.fields.get("blob_path") or "")
    if not raw:
        raise HttpError(404, "no stored original for this document")
    path = Path(raw).resolve()
    if not path.is_relative_to(blob_root()) or not path.is_file():
        raise HttpError(404, "no stored original for this document")
    return path


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """A single `bytes=` range as an inclusive (start, end); None to serve the whole file.

    Raises ValueError when the range cannot be satisfied (416).
    """

    header = header.strip()
    if not header.startswith("bytes=") or "," in header:
        return None  # another unit or several ranges: the whole file is a valid answer
    first, sep, last = header[len("bytes=") :].strip().partition("-")
    first, last = first.strip(), last.strip()
    if not sep or not all(part == "" or part.isdigit() for part in (first, last)):
        return None  # malformed: ignore the header
    if first == "":
        if last == "":
            return None
        length = int(last)
        if length == 0 or size == 0:
            raise ValueError("range not satisfiable")
        return max(0, size - length), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError("range not satisfiable")
    return start, min(end, size - 1)


def _slice(handle: IO[bytes], start: int, length: int) -> Iterator[bytes]:
    try:
        handle.seek(start)
        left = length
        while left > 0:
            chunk = handle.read(min(_CHUNK, left))
            if not chunk:
                break
            left -= len(chunk)
            yield chunk
    finally:
        handle.close()


def serve_original(request: HttpRequest, doc: Document, *, download: bool = False) -> HttpResponse:
    """The response for GET /documents/{id}/original (the reader check is the caller's)."""

    blob = stored_original(doc)
    base, content_type = media_type(doc)
    size = blob.stat().st_size
    inline = base in INLINE_TYPES and not download
    etag = f'"{doc.content_hash}"' if doc.content_hash else ""
    wanted = request.headers.get("Range", "")
    if_range = request.headers.get("If-Range", "")
    try:
        rng = parse_range(wanted, size) if wanted and (not if_range or if_range == etag) else None
    except ValueError:
        resp = HttpResponse(status=416)
        resp["Content-Range"] = f"bytes */{size}"
        resp["Accept-Ranges"] = "bytes"
        return resp
    response: HttpResponse | StreamingHttpResponse
    if rng is None:
        response = FileResponse(blob.open("rb"), content_type=content_type)
        response["Content-Length"] = str(size)
    else:
        start, end = rng
        response = StreamingHttpResponse(
            _slice(blob.open("rb"), start, end - start + 1), status=206, content_type=content_type
        )
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Content-Length"] = str(end - start + 1)
    disposition = content_disposition_header(not inline, original_name(doc, blob))
    if disposition:
        response["Content-Disposition"] = disposition
    response["Accept-Ranges"] = "bytes"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=300"
    if etag:
        response["ETag"] = etag
    if base != "application/pdf":  # browsers refuse to run their PDF viewer in a sandbox
        response["Content-Security-Policy"] = SANDBOX
    audit.info(
        "original served",
        extra={
            "document": doc.id,
            "collection": doc.collection.slug,
            "user": getattr(getattr(request, "user", None), "username", ""),
            "range": wanted if rng else "",
        },
    )
    return response  # type: ignore[return-value]
