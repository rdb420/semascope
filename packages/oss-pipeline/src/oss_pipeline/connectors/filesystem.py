"""Filesystem connector: walk a directory with include and exclude globs.

Settings: `path` (required), `include` (list of globs, default everything),
`exclude` (list of globs), `follow_symlinks` (false), `max_bytes` (512 MiB).
Content hashes are computed on discovery so unchanged files are skipped; a
file whose size and modification time match what the last run recorded is not
read again. The path must lie under the source roots, and files whose symlinks
resolve outside them are skipped (see `guard`). A folder that cannot be listed
or a file that cannot be read is reported, so the run deletes nothing. Items
carry their folder (the source folder's name, then the folders below it) and
extension as path fields.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.base import RunReport, path_fields
from oss_pipeline.connectors.guard import byte_cap, confine_path, source_roots, within

# what the last run recorded for a URI: (size, modified time, content hash)
Known = Callable[[str], "tuple[int | None, datetime | None, str] | None"]

DEFAULT_EXCLUDES = (".*", "*~", "*.tmp", "*.part", "Thumbs.db", "node_modules", ".git", ".venv")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class FilesystemConnector:
    name = "filesystem"
    deletes_missing = True

    def __init__(self) -> None:
        self.report = RunReport()

    def discover(
        self, source: SourceConfig, since: Cursor | None, known: Known | None = None
    ) -> Iterable[SourceItem]:
        self.report = report = RunReport()
        roots = source_roots()
        root = confine_path(str(source.settings["path"]), roots)
        if not root.is_dir():
            raise FileNotFoundError(f"{root} is not a directory")
        include = list(source.settings.get("include") or ["*"])
        exclude = list(source.settings.get("exclude") or []) + list(DEFAULT_EXCLUDES)
        follow = bool(source.settings.get("follow_symlinks", False))
        max_bytes = byte_cap(source.settings, 512 * 1024 * 1024)
        since_ts = since.issued_at.timestamp() if since else None

        def unreadable(exc: OSError) -> None:
            report.skip(str(exc.filename or root), f"{type(exc).__name__}: {exc}")

        for dirpath, dirnames, filenames in os.walk(root, onerror=unreadable, followlinks=follow):
            dirnames[:] = sorted(d for d in dirnames if not _matches(d, exclude))
            for filename in sorted(filenames):
                if _matches(filename, exclude) or not _matches(filename, include):
                    continue
                file = Path(dirpath) / filename
                try:
                    if not within(file.resolve(), roots):
                        continue
                    stat = file.stat()
                except FileNotFoundError:
                    continue  # removed while the run walked past it, or a dangling link
                except OSError as exc:
                    report.skip(str(file), f"{type(exc).__name__}: {exc}")
                    continue
                if not file.is_file() or stat.st_size == 0 or stat.st_size > max_bytes:
                    continue
                if since_ts is not None and stat.st_mtime < since_ts:
                    continue
                uri = file.as_uri()
                modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
                before = known(uri) if known else None
                if before and before[2] and before[0] == stat.st_size and before[1] == modified:
                    digest = before[2]  # same size and time as last run: not read again
                else:
                    try:
                        digest = sha256_file(file)
                    except OSError as exc:
                        report.skip(str(file), f"{type(exc).__name__}: {exc}")
                        continue
                relative = file.relative_to(root)
                yield SourceItem(
                    uri=uri,
                    content_hash=digest,
                    modified_at=modified,
                    size=stat.st_size,
                    metadata={
                        "path": str(file),
                        "relative_path": relative.as_posix(),
                        "fields": path_fields([root.name, *relative.parts[:-1]], filename),
                    },
                )

    def fetch(self, item: SourceItem) -> FetchedItem:
        path = confine_path(str(item.metadata["path"]), source_roots())
        return FetchedItem(item=item, path=str(path))

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        # Deletions are detected by the orchestrator: every known uri of the source
        # that a full discovery pass did not yield. A full pass has no `since`.
        return []


def _matches(name: str, patterns: list[str] | tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
