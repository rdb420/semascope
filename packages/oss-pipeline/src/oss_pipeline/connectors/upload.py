"""Upload connector: files that arrived through the API land in the upload area.

The API writes each uploaded file to `<data>/uploads/<collection>/<sha256>/<name>`
and creates a source item for it. Discovery lists that area so a re-run after
a restart still finds every upload.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.base import RunReport, path_fields, walk_files
from oss_pipeline.connectors.guard import (
    SourceNotAllowed,
    all_upload_roots,
    confine_path,
    confined_files,
    upload_roots,
)

# A collection slug: Django's slug (letters, digits, hyphens, underscores), at most as long as
# Collection.slug. It names a folder, so `.`, `..` and path separators can never be one.
COLLECTION_SLUG = re.compile(r"[-a-zA-Z0-9_]{1,64}")


def upload_root(data_dir: Path, collection: str) -> Path:
    """`<data>/uploads/<collection>`; refuses anything that is not a collection slug."""

    if not isinstance(collection, str) or not COLLECTION_SLUG.fullmatch(collection):
        raise SourceNotAllowed(f"{collection!r} is not a collection slug")
    return data_dir / "uploads" / collection


def store_upload(data_dir: Path, collection: str, name: str, content: bytes) -> Path:
    digest = hashlib.sha256(content).hexdigest()
    safe = Path(name).name
    if safe in ("", ".", ".."):
        safe = "upload"
    dest = upload_root(data_dir, collection) / digest / safe
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(content)
    return dest


class UploadConnector:
    name = "upload"
    deletes_missing = True

    def __init__(self) -> None:
        self.report = RunReport()

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        self.report = RunReport()
        roots = upload_roots(source.collection)
        root = confine_path(str(source.settings["path"]), roots)
        if not root.exists():
            return
        for file in confined_files(walk_files(root, self.report), roots):
            stat = file.stat()
            if stat.st_size == 0:
                continue
            digest = (
                file.parent.name
                if len(file.parent.name) == 64
                else hashlib.sha256(file.read_bytes()).hexdigest()
            )
            yield SourceItem(
                uri=f"upload://{source.collection}/{digest}/{file.name}",
                content_hash=digest,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                size=stat.st_size,
                metadata={"path": str(file), "fields": path_fields([], file.name)},
            )

    def fetch(self, item: SourceItem) -> FetchedItem:
        path = confine_path(str(item.metadata["path"]), all_upload_roots())
        return FetchedItem(item=item, path=str(path))

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []
