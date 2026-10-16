"""Filesystem connector: walk a directory with include and exclude globs.

Settings: `path` (required), `include` (list of globs, default everything),
`exclude` (list of globs), `follow_symlinks` (false), `max_bytes` (512 MiB).
Content hashes are computed on discovery so unchanged files are skipped. The
path must lie under the source roots, and files whose symlinks resolve
outside them are skipped (see `guard`).
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.guard import byte_cap, confine_path, source_roots, within

DEFAULT_EXCLUDES = (".*", "*~", "*.tmp", "*.part", "Thumbs.db", "node_modules", ".git", ".venv")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class FilesystemConnector:
    name = "filesystem"

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        roots = source_roots()
        root = confine_path(str(source.settings["path"]), roots)
        if not root.is_dir():
            raise FileNotFoundError(f"{root} is not a directory")
        include = list(source.settings.get("include") or ["*"])
        exclude = list(source.settings.get("exclude") or []) + list(DEFAULT_EXCLUDES)
        follow = bool(source.settings.get("follow_symlinks", False))
        max_bytes = byte_cap(source.settings, 512 * 1024 * 1024)
        since_ts = since.issued_at.timestamp() if since else None

        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow):
            dirnames[:] = sorted(d for d in dirnames if not _matches(d, exclude))
            for filename in sorted(filenames):
                if _matches(filename, exclude) or not _matches(filename, include):
                    continue
                file = Path(dirpath) / filename
                try:
                    if not within(file.resolve(), roots):
                        continue
                    stat = file.stat()
                except OSError:
                    continue
                if not file.is_file() or stat.st_size == 0 or stat.st_size > max_bytes:
                    continue
                if since_ts is not None and stat.st_mtime < since_ts:
                    continue
                yield SourceItem(
                    uri=file.as_uri(),
                    content_hash=sha256_file(file),
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    size=stat.st_size,
                    metadata={
                        "path": str(file),
                        "relative_path": file.relative_to(root).as_posix(),
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
