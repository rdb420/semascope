"""Upload connector: files that arrived through the API land in the upload area.

The API writes each uploaded file to `<data>/uploads/<collection>/<sha256>/<name>`
and creates a source item for it. Discovery lists that area so a re-run after
a restart still finds every upload.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem


def upload_root(data_dir: Path, collection: str) -> Path:
    return data_dir / "uploads" / collection


def store_upload(data_dir: Path, collection: str, name: str, content: bytes) -> Path:
    digest = hashlib.sha256(content).hexdigest()
    safe = Path(name).name or "upload"
    dest = upload_root(data_dir, collection) / digest / safe
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(content)
    return dest


class UploadConnector:
    name = "upload"

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        root = Path(str(source.settings["path"]))
        if not root.exists():
            return
        for file in sorted(p for p in root.rglob("*") if p.is_file()):
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
                metadata={"path": str(file)},
            )

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(item=item, path=str(item.metadata["path"]))

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []
