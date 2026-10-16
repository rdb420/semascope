"""Mail connector: mbox files and .eml messages; attachments become child documents.

Settings: `path` (a folder or a single mbox/eml file), `attachments` (true).
Each message is written out as RFC 822 for Tika to parse; each attachment is
written beside it and yields its own item with `parent_uri` in metadata.

A message's URI is `mail:<Message-ID>#<identity>`, the identity being a hash
of the message without the flags mail clients rewrite (`Status`, `X-Status`,
...): two different messages that share a Message-ID (a forgery, a broken
client) are two documents, and the same message in two mailboxes is one.
Attachments are `<message uri>/<file name>`, numbered when a message has two
of the same name. Files are read as mbox when they end in `.mbox` or `mbox`, or
when they have no suffix and start with an mbox `From ` line; as a message when
they end in `.eml`.
"""

from __future__ import annotations

import email
import hashlib
import mailbox
from collections.abc import Iterable, Iterator
from datetime import UTC
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.base import RunReport, walk_files, work_dir
from oss_pipeline.connectors.guard import confine_path, confined_files, source_roots

CLIENT_FLAGS = (
    "Status",
    "X-Status",
    "X-Keywords",
    "X-UID",
    "X-Mozilla-Status",
    "X-Mozilla-Status2",
)


def is_mbox(path: Path) -> bool:
    name = path.name.lower()
    if name.endswith(".mbox") or name.endswith("mbox"):
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as fh:
            return fh.read(5) == b"From "
    except OSError:
        return False


def _messages(path: Path, roots: list[Path], report: RunReport) -> Iterator[tuple[str, Message]]:
    if path.is_dir():
        for file in confined_files(walk_files(path, report), roots):
            yield from _messages(file, roots, report)
        return
    try:
        if path.suffix.lower() == ".eml":
            with path.open("rb") as fh:
                yield str(path), email.message_from_binary_file(fh)
        elif is_mbox(path):
            box = mailbox.mbox(str(path), create=False)
            for i, msg in enumerate(box):
                yield f"{path}#{i}", msg
    except (OSError, mailbox.Error, UnicodeError) as exc:
        report.skip(str(path), f"{type(exc).__name__}: {exc}")


def identity(msg: Message) -> str:
    """A hash of the message as sent: without the flags a mail client rewrites."""

    copy = email.message_from_bytes(msg.as_bytes())
    for header in CLIENT_FLAGS:
        del copy[header]
    return hashlib.sha256(copy.as_bytes()).hexdigest()


def _unique(name: str, used: set[str]) -> str:
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, ""
    candidate, n = name, 1
    while candidate in used:
        n += 1
        candidate = f"{stem}-{n}.{suffix}" if suffix else f"{stem}-{n}"
    used.add(candidate)
    return candidate


class MailConnector:
    name = "mail"
    deletes_missing = True

    def __init__(self) -> None:
        self.report = RunReport()

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        self.report = report = RunReport()
        roots = source_roots()
        root = confine_path(str(source.settings["path"]), roots)
        if not root.exists():
            raise FileNotFoundError(f"{root} does not exist")
        want_attachments = bool(source.settings.get("attachments", True))
        tmp = work_dir(source, "mail")
        for locator, msg in _messages(root, roots, report):
            raw = msg.as_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            message_id = str(msg.get("Message-ID") or "").strip("<> ")
            ident = identity(msg)[:16]
            uri = f"mail:{message_id}#{ident}" if message_id else f"mail:{ident}"
            out = tmp / f"{digest}.eml"
            if not out.exists():
                out.write_bytes(raw)
            date = None
            if msg.get("Date"):
                try:
                    date = parsedate_to_datetime(str(msg["Date"]))
                    date = date.astimezone(UTC) if date.tzinfo else date.replace(tzinfo=UTC)
                except (TypeError, ValueError):
                    date = None
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
                    # its attachments are documents of their own: not repeated in its text
                    "children_expanded": want_attachments,
                },
            )
            if not want_attachments:
                continue
            used: set[str] = set()
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
                name = _unique(Path(part.get_filename() or "").name or f"attachment-{n}", used)
                adigest = hashlib.sha256(payload).hexdigest()
                apath = tmp / f"{adigest}-{name}"
                if not apath.exists():
                    apath.write_bytes(payload)
                yield SourceItem(
                    uri=f"{uri}/{name}",
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
