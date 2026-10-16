"""Mail connector: mbox files and .eml messages; attachments become child documents.

Settings: `path` (a folder or a single mbox/eml file), `attachments` (true).
Each message is written out as RFC 822 for Tika to parse; each attachment is
written beside it and yields its own item with `parent_uri` in metadata.
"""

from __future__ import annotations

import email
import hashlib
import mailbox
import tempfile
from collections.abc import Iterable, Iterator
from datetime import UTC
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.guard import confine_path, confined_files, source_roots


def _messages(path: Path, roots: list[Path]) -> Iterator[tuple[str, Message]]:
    if path.is_dir():
        for file in confined_files(sorted(path.rglob("*")), roots):
            if file.is_file():
                yield from _messages(file, roots)
        return
    suffix = path.suffix.lower()
    if suffix == ".eml":
        with path.open("rb") as fh:
            yield str(path), email.message_from_binary_file(fh)
    elif suffix in (".mbox", "") or path.name.lower().endswith("mbox"):
        try:
            box = mailbox.mbox(str(path))
        except Exception:  # noqa: BLE001 - not an mbox after all
            return
        for i, msg in enumerate(box):
            yield f"{path}#{i}", msg


class MailConnector:
    name = "mail"

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        roots = source_roots()
        root = confine_path(str(source.settings["path"]), roots)
        if not root.exists():
            raise FileNotFoundError(f"{root} does not exist")
        want_attachments = bool(source.settings.get("attachments", True))
        tmp = Path(tempfile.gettempdir()) / "semascope-mail"
        tmp.mkdir(parents=True, exist_ok=True)
        for locator, msg in _messages(root, roots):
            raw = msg.as_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            message_id = str(msg.get("Message-ID") or "").strip("<> ") or digest[:16]
            out = tmp / f"{digest}.eml"
            out.write_bytes(raw)
            date = None
            if msg.get("Date"):
                try:
                    date = parsedate_to_datetime(str(msg["Date"]))
                    date = date.astimezone(UTC) if date.tzinfo else date.replace(tzinfo=UTC)
                except (TypeError, ValueError):
                    date = None
            uri = f"mail:{message_id}"
            yield SourceItem(
                uri=uri,
                content_hash=digest,
                modified_at=date,
                size=len(raw),
                metadata={
                    "path": str(out),
                    "media_type": "message/rfc822",
                    "locator": locator,
                    "subject": str(msg.get("Subject") or ""),
                },
            )
            if not want_attachments:
                continue
            for n, part in enumerate(msg.walk()):
                if (
                    part.get_content_maintype() == "multipart"
                    or part.get("Content-Disposition") is None
                ):
                    continue
                if "attachment" not in str(part.get("Content-Disposition")).lower():
                    continue
                payload = part.get_payload(decode=True)
                if not isinstance(payload, bytes) or not payload:
                    continue
                name = part.get_filename() or f"attachment-{n}"
                adigest = hashlib.sha256(payload).hexdigest()
                apath = tmp / f"{adigest}-{Path(name).name}"
                apath.write_bytes(payload)
                yield SourceItem(
                    uri=f"{uri}/{Path(name).name}",
                    content_hash=adigest,
                    modified_at=date,
                    size=len(payload),
                    metadata={
                        "path": str(apath),
                        "media_type": part.get_content_type(),
                        "parent_uri": uri,
                    },
                )

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(
            item=item, path=str(item.metadata["path"]), media_type=item.metadata.get("media_type")
        )

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []
