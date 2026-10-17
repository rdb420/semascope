"""PST connector: Outlook data files (`.pst`, `.ost`) as mail documents, with attachments.

Needs the optional `pst` extra: `libpff-python` (the `pypff` module of libyal's libpff),
licensed LGPL-3.0-or-later and therefore never a required dependency
(docs/adr/0015-pst-libpff-extra.md). Without it a PST source fails its run with a message
saying so; nothing is deleted.

Settings: `path` (a folder or one file, under the source roots), `attachments` (true),
`max_messages` (100,000 per run; a run that stops there deletes nothing by absence) and
`max_attachment_bytes` (50 MiB; a larger attachment is skipped).

Each message is written out as an RFC 822 message (its transport headers when the PST kept
them, else From, Subject and Date from its properties; its plain-text and HTML bodies) for
Tika to parse, under the URI `<pst file URI>!/<folder>/<folder>/<message id>`; each
attachment is a child document `<message URI>/<file name>` with `parent_uri`. The folder
facet shows the PST file and its folders. A PST, folder or message that cannot be read is
reported, so the run deletes nothing.
"""

from __future__ import annotations

import email.policy
import hashlib
import os
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from email.message import EmailMessage
from email.parser import HeaderParser
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.base import RunReport, path_fields, walk_files, work_dir
from oss_pipeline.connectors.guard import confine_path, confined_files, source_roots

PST_SUFFIXES = (".pst", ".ost")
CHUNK = 1 << 20
DEFAULTS = {"max_messages": 100_000, "max_attachment_bytes": 50 * 1024 * 1024}
KEEP_HEADERS = (
    "From",
    "To",
    "Cc",
    "Date",
    "Subject",
    "Message-ID",
    "In-Reply-To",
    "References",
    "Reply-To",
)
MISSING_EXTRA = (
    "PST sources need the optional `pst` extra (libpff-python, LGPL-3.0-or-later), which "
    "this installation does not have; see docs/adr/0015-pst-libpff-extra.md"
)


class MissingExtra(RuntimeError):  # noqa: N818 - reads better at the raise site
    pass


def _pypff() -> Any:
    try:
        import pypff
    except ImportError as exc:
        raise MissingExtra(MISSING_EXTRA) from exc
    return pypff


def is_pst(path: Path) -> bool:
    return path.name.lower().endswith(PST_SUFFIXES)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for codec in ("utf-8", "cp1252"):
            try:
                return value.decode(codec).replace("\x00", "")
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", "replace")
    return str(value).replace("\x00", "")


def _when(message: Any) -> datetime | None:
    for attr in ("delivery_time", "client_submit_time", "creation_time"):
        try:
            value = getattr(message, attr, None)
        except OSError:
            value = None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


def as_rfc822(message: Any) -> EmailMessage:
    """A PST message as an e-mail message: headers, plain-text and HTML bodies."""

    out = EmailMessage(policy=email.policy.SMTP)
    headers = (
        HeaderParser().parsestr(_text(message.transport_headers))
        if message.transport_headers
        else None
    )
    for name in KEEP_HEADERS:
        value = headers.get(name) if headers is not None else None
        if value:
            try:
                out[name] = " ".join(str(value).split())  # unfolded
            except (ValueError, TypeError):
                continue
    if "Subject" not in out and message.subject:
        out["Subject"] = _text(message.subject)
    if "From" not in out and message.sender_name:
        out["From"] = _text(message.sender_name)
    when = _when(message)
    if "Date" not in out and when is not None:
        out["Date"] = format_datetime(when)
    plain = _text(message.plain_text_body)
    markup = _text(message.html_body)
    out.set_content(plain or ("" if markup else " "))
    if markup:
        out.add_alternative(markup, subtype="html")
    return out


def _folders(
    folder: Any, trail: list[str], report: RunReport, where: str
) -> Iterator[tuple[list[str], Any]]:
    """(folder names from the top, message) for every message, depth first."""

    try:
        count = folder.number_of_sub_messages
    except OSError as exc:
        report.skip(f"{where}!/{'/'.join(trail)}", f"{type(exc).__name__}: {exc}")
        count = 0
    for i in range(count):
        try:
            yield trail, folder.get_sub_message(i)
        except OSError as exc:
            report.skip(f"{where}!/{'/'.join(trail)}#{i}", f"{type(exc).__name__}: {exc}")
    try:
        subfolders = folder.number_of_sub_folders
    except OSError as exc:
        report.skip(f"{where}!/{'/'.join(trail)}", f"{type(exc).__name__}: {exc}")
        return
    for i in range(subfolders):
        try:
            sub = folder.get_sub_folder(i)
            name = _text(sub.name).replace("/", "_").strip() or f"folder-{i}"
        except OSError as exc:
            report.skip(f"{where}!/{'/'.join(trail)}/{i}", f"{type(exc).__name__}: {exc}")
            continue
        yield from _folders(sub, [*trail, name], report, where)


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


def _save_attachment(attachment: Any, tmp: Path, limit: int) -> tuple[Path, str, int] | None:
    """Stream an attachment to the work area; None when it is over the limit or empty."""

    size = int(attachment.get_size() or 0)
    if size <= 0 or size > limit:
        return None
    h = hashlib.sha256()
    part = tmp / f".part-{os.getpid()}-{id(attachment)}"
    written = 0
    try:
        with part.open("wb") as out:
            attachment.seek_offset(0)
            while written < size:
                chunk = attachment.read_buffer(min(CHUNK, size - written))
                if not chunk:
                    break
                written += len(chunk)
                h.update(chunk)
                out.write(chunk)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return part, h.hexdigest(), written


class PstConnector:
    name = "pst"
    deletes_missing = True

    def __init__(self) -> None:
        self.report = RunReport()

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        self.report = report = RunReport()
        pypff = _pypff()
        roots = source_roots()
        root = confine_path(str(source.settings.get("path") or ""), roots)
        if not root.exists():
            raise FileNotFoundError(f"{root} does not exist")
        limits = {k: int(source.settings.get(k, v)) for k, v in DEFAULTS.items()}
        want_attachments = bool(source.settings.get("attachments", True))
        files = [f for f in walk_files(root, report) if is_pst(f) or f == root]
        tmp = work_dir(source, "pst")
        left = limits["max_messages"]
        for file in confined_files(files, roots):
            where = file.as_uri()
            pst = pypff.file()
            try:
                pst.open(str(file))
                top = pst.get_root_folder()
            except OSError as exc:
                report.skip(where, f"{type(exc).__name__}: {exc}")
                continue
            try:
                for trail, message in _folders(top, [], report, where):
                    if left <= 0:
                        report.truncated = True
                        return
                    try:
                        items = self._message(message, trail, file, tmp, limits, want_attachments)
                    except OSError as exc:
                        report.skip(f"{where}!/{'/'.join(trail)}", f"{type(exc).__name__}: {exc}")
                        continue
                    left -= 1
                    yield from items
            finally:
                pst.close()

    def _message(
        self,
        message: Any,
        trail: list[str],
        file: Path,
        tmp: Path,
        limits: dict[str, int],
        want_attachments: bool,
    ) -> list[SourceItem]:
        raw = as_rfc822(message).as_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        out_path = tmp / f"{digest}.eml"
        if not out_path.exists():
            out_path.write_bytes(raw)
        uri = f"{file.as_uri()}!/{'/'.join([*trail, str(message.identifier)])}"
        when = _when(message)
        folders = [file.name, *trail]
        items = [
            SourceItem(
                uri=uri,
                content_hash=digest,
                modified_at=when,
                size=len(raw),
                metadata={
                    "path": str(out_path),
                    "media_type": "message/rfc822",
                    "subject": _text(message.subject),
                    "children_expanded": want_attachments,
                    "fields": path_fields(folders, f"{message.identifier}.eml"),
                },
            )
        ]
        if not want_attachments:
            return items
        used: set[str] = set()
        for n in range(message.number_of_attachments):
            attachment = message.get_attachment(n)
            saved = _save_attachment(attachment, tmp, limits["max_attachment_bytes"])
            if saved is None:
                continue
            part, adigest, size = saved
            name = _unique(Path(_text(attachment.long_filename)).name or f"attachment-{n}", used)
            apath = tmp / f"{adigest}-{name[:100]}"
            part.replace(apath)
            items.append(
                SourceItem(
                    uri=f"{uri}/{name}",
                    content_hash=adigest,
                    modified_at=when,
                    size=size,
                    metadata={
                        "path": str(apath),
                        "parent_uri": uri,
                        "fields": path_fields(folders, name),
                    },
                )
            )
        return items

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(
            item=item, path=str(item.metadata["path"]), media_type=item.metadata.get("media_type")
        )

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []
