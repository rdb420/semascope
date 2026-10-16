"""Archive connector: members of zip and tar files as documents, with limits.

Settings: `path` (folder or archive file), `max_members` (10000),
`max_member_bytes` (256 MiB), `max_ratio` (100, decompression bomb guard),
`max_depth` (2, nested archives). Members keep `parent_uri` in metadata.
"""

from __future__ import annotations

import hashlib
import tarfile
import tempfile
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.guard import confine_path, confined_files, source_roots

ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")


def is_archive(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(s) for s in ARCHIVE_SUFFIXES)


class ArchiveConnector:
    name = "archive"

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        roots = source_roots()
        root = confine_path(str(source.settings["path"]), roots)
        limits = {
            "max_members": int(source.settings.get("max_members", 10000)),
            "max_member_bytes": int(source.settings.get("max_member_bytes", 256 * 1024 * 1024)),
            "max_ratio": int(source.settings.get("max_ratio", 100)),
            "max_depth": int(source.settings.get("max_depth", 2)),
        }
        files = (
            [root]
            if root.is_file()
            else sorted(p for p in root.rglob("*") if p.is_file() and is_archive(p))
        )
        files = list(confined_files(files, roots))
        tmp = Path(tempfile.gettempdir()) / "semascope-archive"
        tmp.mkdir(parents=True, exist_ok=True)
        for file in files:
            yield from _expand(file, file.as_uri(), tmp, limits, depth=0)

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(item=item, path=str(item.metadata["path"]))

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []


def _expand(
    archive: Path, parent_uri: str, tmp: Path, limits: dict[str, int], *, depth: int
) -> Iterable[SourceItem]:
    archive_size = max(archive.stat().st_size, 1)
    total = 0
    count = 0
    members: list[tuple[str, bytes, datetime | None]] = []
    try:
        if archive.name.lower().endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    if info.is_dir() or count >= limits["max_members"]:
                        continue
                    if info.file_size > limits["max_member_bytes"]:
                        continue
                    total += info.file_size
                    if total > archive_size * limits["max_ratio"]:
                        break
                    count += 1
                    mtime = (
                        datetime(*info.date_time, tzinfo=UTC) if info.date_time[0] >= 1980 else None
                    )
                    members.append((info.filename, zf.read(info), mtime))
        else:
            with tarfile.open(archive) as tf:
                for tinfo in tf:
                    if not tinfo.isfile() or count >= limits["max_members"]:
                        continue
                    if tinfo.size > limits["max_member_bytes"]:
                        continue
                    total += tinfo.size
                    if total > archive_size * limits["max_ratio"]:
                        break
                    extracted = tf.extractfile(tinfo)
                    if extracted is None:
                        continue
                    count += 1
                    members.append(
                        (tinfo.name, extracted.read(), datetime.fromtimestamp(tinfo.mtime, tz=UTC))
                    )
    except (zipfile.BadZipFile, tarfile.TarError, OSError):
        return
    for name, content, mtime in members:
        safe = Path(name).name
        if not safe or safe.startswith("."):
            continue
        digest = hashlib.sha256(content).hexdigest()
        out = tmp / f"{digest}-{safe}"
        out.write_bytes(content)
        uri = f"{parent_uri}!/{name}"
        if is_archive(Path(safe)) and depth < limits["max_depth"]:
            yield from _expand(out, uri, tmp, limits, depth=depth + 1)
            continue
        yield SourceItem(
            uri=uri,
            content_hash=digest,
            modified_at=mtime,
            size=len(content),
            metadata={"path": str(out), "parent_uri": parent_uri, "member": name},
        )
