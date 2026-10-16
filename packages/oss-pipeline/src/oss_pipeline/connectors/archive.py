"""Archive connector: members of zip and tar files as documents, with limits.

Settings: `path` (folder or archive file), and the limits, each counted on the
bytes actually decompressed, never on what an archive's headers claim:

- `max_member_bytes` (256 MiB): a larger member is skipped;
- `max_ratio` (100): everything expanded from one archive, nested archives
  included, stops at this many times the archive's size on disk, and a zip
  member whose header claims a higher ratio is skipped;
- `max_total_bytes` (4 GiB): everything expanded from one archive, across all
  nesting levels, stops here too;
- `max_members` (10000): members taken from one archive, across all levels;
- `max_depth` (2): archives inside archives are expanded this many levels
  deep; deeper ones are indexed as files.

Members are streamed to the source's work area one at a time (never held in
memory), and a nested archive's copy is removed once it has been expanded.
Members keep `parent_uri` in metadata. An archive that cannot be read (corrupt,
or still being copied in) is reported, so the run deletes nothing.
"""

from __future__ import annotations

import hashlib
import os
import tarfile
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.base import RunReport, path_fields, work_dir
from oss_pipeline.connectors.guard import confine_path, confined_files, source_roots

ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
CHUNK = 1 << 20
DEFAULTS = {
    "max_members": 10_000,
    "max_member_bytes": 256 * 1024 * 1024,
    "max_ratio": 100,
    "max_total_bytes": 4 * 1024 * 1024 * 1024,
    "max_depth": 2,
}


def is_archive(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(s) for s in ARCHIVE_SUFFIXES)


@dataclass
class Budget:
    """What is left to expand from one top-level archive, nested archives included."""

    bytes_left: int
    members_left: int


class OverBudget(Exception):  # noqa: N818
    pass


class ArchiveConnector:
    name = "archive"
    deletes_missing = True

    def __init__(self) -> None:
        self.report = RunReport()

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        self.report = RunReport()
        roots = source_roots()
        root = confine_path(str(source.settings["path"]), roots)
        limits = {k: int(source.settings.get(k, v)) for k, v in DEFAULTS.items()}
        files = (
            [root]
            if root.is_file()
            else sorted(p for p in root.rglob("*") if p.is_file() and is_archive(p))
        )
        files = list(confined_files(files, roots))
        tmp = work_dir(source, "archive")
        for file in files:
            size = max(file.stat().st_size, 1)
            budget = Budget(
                bytes_left=min(limits["max_total_bytes"], size * limits["max_ratio"]),
                members_left=limits["max_members"],
            )
            folders = [root.name, *file.relative_to(root).parts] if root.is_dir() else [file.name]
            yield from _expand(
                file, file.as_uri(), folders, tmp, limits, budget, self.report, depth=0
            )

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(item=item, path=str(item.metadata["path"]))

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []


def _members(archive: Path, limits: dict[str, int]) -> Iterator[tuple[str, int, Any, Any]]:
    """(name, declared size, mtime, opener) for each regular member, in archive order."""

    if archive.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.file_size > limits["max_ratio"] * max(info.compress_size, 1):
                    continue  # a member claiming a ratio above the limit is a bomb
                mtime = datetime(*info.date_time, tzinfo=UTC) if info.date_time[0] >= 1980 else None
                yield info.filename, info.file_size, mtime, lambda i=info: zf.open(i)
    else:
        with tarfile.open(archive) as tf:
            for tinfo in tf:
                if not tinfo.isfile():
                    continue  # links, devices and folders are never members

                def opener(t: tarfile.TarInfo = tinfo) -> IO[bytes]:
                    extracted = tf.extractfile(t)
                    if extracted is None:
                        raise tarfile.TarError(f"{t.name} cannot be read")
                    return extracted

                yield tinfo.name, tinfo.size, datetime.fromtimestamp(tinfo.mtime, tz=UTC), opener


def _stream(source: IO[bytes], tmp: Path, limit: int, budget: Budget) -> tuple[Path, str, int]:
    """Copy a member to a temporary file, counting what is really decompressed."""

    h = hashlib.sha256()
    written = 0
    part = tmp / f".part-{os.getpid()}-{id(source)}"
    try:
        with part.open("wb") as out:
            while chunk := source.read(CHUNK):
                written += len(chunk)
                if written > limit:
                    raise OverBudget("member over max_member_bytes")
                if written > budget.bytes_left:
                    raise OverBudget("archive over its expansion budget")
                h.update(chunk)
                out.write(chunk)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    budget.bytes_left -= written
    return part, h.hexdigest(), written


def _expand(
    archive: Path,
    parent_uri: str,
    folders: list[str],
    tmp: Path,
    limits: dict[str, int],
    budget: Budget,
    report: RunReport,
    *,
    depth: int,
) -> Iterator[SourceItem]:
    try:
        for name, declared, mtime, opener in _members(archive, limits):
            safe = Path(name).name
            if not safe or safe.startswith("."):
                continue
            if declared > limits["max_member_bytes"] or declared > budget.bytes_left:
                continue
            if budget.members_left <= 0:
                return  # the archive's member limit: the rest is never indexed
            budget.members_left -= 1
            try:
                with opener() as stream:
                    part, digest, written = _stream(stream, tmp, limits["max_member_bytes"], budget)
            except OverBudget as exc:
                if "budget" in str(exc):
                    return  # nothing more from this archive
                continue
            except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as exc:
                report.skip(f"{parent_uri}!/{name}", f"{type(exc).__name__}: {exc}")
                continue
            uri = f"{parent_uri}!/{name}"
            inner = [*folders, *Path(name).parts[:-1]]
            if is_archive(Path(safe)) and depth < limits["max_depth"]:
                nested = tmp / f".nested-{digest}-{safe}"
                part.replace(nested)
                try:
                    yield from _expand(
                        nested, uri, [*inner, safe], tmp, limits, budget, report, depth=depth + 1
                    )
                finally:
                    nested.unlink(missing_ok=True)
                continue
            out = tmp / f"{digest}-{safe}"
            part.replace(out)
            yield SourceItem(
                uri=uri,
                content_hash=digest,
                modified_at=mtime,
                size=written,
                metadata={
                    "path": str(out),
                    "parent_uri": parent_uri,
                    "member": name,
                    "fields": path_fields(inner, safe),
                },
            )
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as exc:
        report.skip(parent_uri, f"{type(exc).__name__}: {exc}")
