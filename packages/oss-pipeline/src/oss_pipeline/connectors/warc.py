"""WARC connector: web archives (`.warc`, `.warc.gz`) as documents, one per archived page.

Settings: `path` (a folder or one archive file, under the source roots), and limits:

- `max_record_bytes` (50 MiB): a larger payload is skipped;
- `max_records` (100,000): documents taken from one run, across the source's archives; a
  run that stops there deletes nothing by absence.

Each `response` record with a 2xx status, and each `resource` record, becomes a document:
its URI is the archived page's original URL (`WARC-Target-URI`), its modified time the
capture date (`WARC-Date`), its media type the archived `Content-Type`, and its text the
payload as the server sent it, with chunked transfer and gzip or deflate content encoding
undone. Requests, metadata, revisit records, redirects and error pages are left out. When
an archive holds the same URL more than once, the latest capture is the document. The
payload is streamed to the source's work area; the folder facet shows host and path.
An archive that cannot be read is reported, so the run deletes nothing.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO, Any
from urllib.parse import urlsplit

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.base import RunReport, path_fields, walk_files, work_dir
from oss_pipeline.connectors.guard import confine_path, confined_files, source_roots

WARC_SUFFIXES = (".warc", ".warc.gz")
CHUNK = 1 << 20
DEFAULTS = {"max_record_bytes": 50 * 1024 * 1024, "max_records": 100_000}


def is_warc(path: Path) -> bool:
    return path.name.lower().endswith(WARC_SUFFIXES)


class TooBig(Exception):  # noqa: N818
    pass


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _wanted(record: Any) -> bool:
    """A page capture: a 2xx response, or a resource record (a file fetched without HTTP)."""

    if record.rec_type == "resource":
        return True
    if record.rec_type != "response" or record.http_headers is None:
        return False
    status = str(record.http_headers.get_statuscode() or "")
    return status.startswith("2")


def _media_type(record: Any) -> str | None:
    if record.http_headers is not None:
        value = record.http_headers.get_header("Content-Type")
    else:
        value = record.rec_headers.get_header("Content-Type")
    mt = str(value or "").split(";")[0].strip().lower()
    return mt or None


def _records(path: Path) -> Iterator[tuple[str, Any]]:
    """(record id, record) for every record of an archive, in order."""

    from warcio.archiveiterator import ArchiveIterator

    with path.open("rb") as fh:
        for record in ArchiveIterator(fh):
            yield str(record.rec_headers.get_header("WARC-Record-ID") or ""), record


def _latest(files: list[Path], report: RunReport) -> dict[str, tuple[Path, str, datetime | None]]:
    """The latest wanted capture of each URL across the archives: URL -> (file, record, date)."""

    from warcio.exceptions import ArchiveLoadFailed

    latest: dict[str, tuple[Path, str, datetime | None]] = {}
    for file in files:
        try:
            for record_id, record in _records(file):
                if not record_id or not record.rec_type:
                    report.skip(file.as_uri(), "a record without a WARC-Record-ID or type")
                    break  # the rest of a damaged archive is not trusted
                if not _wanted(record):
                    continue
                uri = str(record.rec_headers.get_header("WARC-Target-URI") or "").strip("<> ")
                if not uri:
                    continue
                date = _date(record.rec_headers.get_header("WARC-Date"))
                known = latest.get(uri)
                if known is None or (date and (known[2] is None or date >= known[2])):
                    latest[uri] = (file, record_id, date)
        except (ArchiveLoadFailed, OSError, EOFError, ValueError) as exc:
            report.skip(file.as_uri(), f"{type(exc).__name__}: {exc}")
    return latest


def _stream(source: IO[bytes], tmp: Path, limit: int) -> tuple[Path, str, int]:
    h = hashlib.sha256()
    written = 0
    part = tmp / f".part-{os.getpid()}-{id(source)}"
    try:
        with part.open("wb") as out:
            while chunk := source.read(CHUNK):
                written += len(chunk)
                if written > limit:
                    raise TooBig
                h.update(chunk)
                out.write(chunk)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return part, h.hexdigest(), written


def _name(uri: str, media_type: str | None) -> tuple[list[str], str]:
    """The folders (host, then the URL's path) and a file name with a useful suffix."""

    parts = urlsplit(uri)
    segments = [s for s in PurePosixPath(parts.path or "/").parts if s not in ("/", "")]
    name = segments[-1] if segments and not parts.path.endswith("/") else "index"
    folders = [parts.hostname or "", *(segments[:-1] if name != "index" else segments)]
    if "." not in name and media_type:
        name += mimetypes.guess_extension(media_type) or ""
    return folders, name


class WarcConnector:
    name = "warc"
    deletes_missing = True

    def __init__(self) -> None:
        self.report = RunReport()

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        from warcio.exceptions import ArchiveLoadFailed

        self.report = report = RunReport()
        roots = source_roots()
        root = confine_path(str(source.settings.get("path") or ""), roots)
        if not root.exists():
            raise FileNotFoundError(f"{root} does not exist")
        limits = {k: int(source.settings.get(k, v)) for k, v in DEFAULTS.items()}
        files = [f for f in walk_files(root, report) if is_warc(f) or f == root]
        files = list(confined_files(files, roots))
        chosen = {(f, rid): uri for uri, (f, rid, _d) in _latest(files, report).items()}
        tmp = work_dir(source, "warc")
        left = limits["max_records"]
        holding = {f for f, _ in chosen}
        for file in files:
            if file not in holding:
                continue
            try:
                for record_id, record in _records(file):
                    uri = chosen.get((file, record_id))
                    if uri is None:
                        continue
                    if left <= 0:
                        report.truncated = True  # past max_records: nothing deleted by absence
                        return
                    media_type = _media_type(record)
                    try:
                        part, digest, size = _stream(
                            record.content_stream(), tmp, limits["max_record_bytes"]
                        )
                    except TooBig:
                        continue  # over max_record_bytes: not indexed, like an archive member
                    folders, name = _name(uri, media_type)
                    out = tmp / f"{digest}-{Path(name).name[:100]}"
                    part.replace(out)
                    left -= 1
                    yield SourceItem(
                        uri=uri,
                        content_hash=digest,
                        modified_at=_date(record.rec_headers.get_header("WARC-Date")),
                        size=size,
                        metadata={
                            "path": str(out),
                            "media_type": media_type,
                            "parent_uri": file.as_uri(),
                            "fields": path_fields(folders, name),
                        },
                    )
            except (ArchiveLoadFailed, OSError, EOFError, ValueError) as exc:
                report.skip(file.as_uri(), f"{type(exc).__name__}: {exc}")

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(
            item=item, path=str(item.metadata["path"]), media_type=item.metadata.get("media_type")
        )

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []
