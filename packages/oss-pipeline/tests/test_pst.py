"""PST connector: messages and attachments from an Outlook data file, through libpff.

libpff's binding (`pypff`, the LGPL `pst` extra) is not installed in the default
environment, and no PST can be written from Python, so these tests give the connector a
stand-in module with libpff's interface. The last tests run against the real binding when
it is installed (`uv run --with libpff-python pytest ...`): its interface is what the
connector uses, and a file that is not a PST is reported, not fatal.
"""

from __future__ import annotations

import email
import email.policy
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from oss_core.contracts import SourceConfig
from oss_pipeline.connectors import pst as pst_module
from oss_pipeline.connectors.pst import MissingExtra, PstConnector
from oss_pipeline.registry import connectors


class Attachment:
    def __init__(self, name: str | None, data: bytes) -> None:
        self.long_filename = name
        self.data = data
        self.pos = 0

    def get_size(self) -> int:
        return len(self.data)

    def seek_offset(self, offset: int) -> None:
        self.pos = offset

    def read_buffer(self, size: int) -> bytes:
        chunk = self.data[self.pos : self.pos + size]
        self.pos += len(chunk)
        return chunk


class Message:
    def __init__(self, identifier: int, subject: str, **kw: Any) -> None:
        self.identifier = identifier
        self.subject = subject
        self.sender_name = kw.get("sender_name", "Pat Field")
        self.transport_headers = kw.get("transport_headers")
        self.plain_text_body = kw.get("plain", b"")
        self.html_body = kw.get("html")
        self.delivery_time = kw.get("when")
        self.client_submit_time = None
        self.creation_time = None
        self._attachments = kw.get("attachments", [])

    @property
    def number_of_attachments(self) -> int:
        return len(self._attachments)

    def get_attachment(self, i: int) -> Attachment:
        found: Attachment = self._attachments[i]
        return found


class Folder:
    def __init__(self, name: str, messages: list[Any], folders: list[Folder] | None = None):
        self.name = name
        self.messages = messages
        self.folders = folders or []

    @property
    def number_of_sub_messages(self) -> int:
        return len(self.messages)

    def get_sub_message(self, i: int) -> Message:
        if isinstance(self.messages[i], Exception):
            raise self.messages[i]
        return self.messages[i]  # type: ignore[no-any-return]

    @property
    def number_of_sub_folders(self) -> int:
        return len(self.folders)

    def get_sub_folder(self, i: int) -> Folder:
        return self.folders[i]


HEADERS = (
    "From: Pat Field <pat@example.org>\r\nTo: Sam Reed <sam@example.org>\r\n"
    "Subject: Heron survey\r\n results\r\nMessage-ID: <survey-1@example.org>\r\n"
    "Date: Wed, 15 Oct 2026 10:00:00 +1000\r\nX-Mailer: Outlook\r\n"
)
MAILBOXES: dict[str, Folder] = {}


def mailbox() -> Folder:
    inbox = Folder(
        "Inbox",
        [
            Message(
                2097188,
                "Heron survey results",
                transport_headers=HEADERS,
                plain=b"Twelve herons on the lagoon.",
                html=b"<p>Twelve <b>herons</b> on the lagoon.</p>",
                attachments=[
                    Attachment("counts.csv", b"species,count\nheron,12\n"),
                    Attachment("counts.csv", b"species,count\nheron,13\n"),
                    Attachment(None, b"%PDF-1.4 unnamed"),
                    Attachment("huge.bin", b"x" * 5000),
                ],
            ),
            OSError("unable to read message 1"),
        ],
    )
    sent = Folder(
        "Sent Items",
        [
            Message(
                2097220,
                "Re: Heron survey",
                plain="Thanks — noted.".encode("cp1252"),
                when=datetime(2026, 10, 16, 9, 30, tzinfo=UTC),
            )
        ],
    )
    return Folder("", [], [Folder("Top of Personal Folders", [], [inbox, sent])])


class File:
    def __init__(self) -> None:
        self.root: Folder | None = None
        self.closed = False

    def open(self, path: str) -> None:
        if Path(path).read_bytes().startswith(b"BROKEN"):
            raise OSError("pypff_file_open: unable to open file")
        self.root = MAILBOXES[Path(path).name]

    def get_root_folder(self) -> Folder:
        assert self.root is not None
        return self.root

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_pypff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pypff", types.SimpleNamespace(file=File))
    monkeypatch.setitem(MAILBOXES, "archive.pst", mailbox())


def source(path: Path, **settings: Any) -> SourceConfig:
    return SourceConfig(
        id="p", collection="c", connector="pst", settings={"path": str(path), **settings}
    )


def test_pst_is_registered() -> None:
    assert "pst" in connectors()


def test_messages_and_attachments(tmp_path: Path, fake_pypff: None) -> None:
    (tmp_path / "archive.pst").write_bytes(b"!BDN fake")
    connector = PstConnector()
    items = list(connector.discover(source(tmp_path, max_attachment_bytes=1000), None))
    base = f"{(tmp_path / 'archive.pst').as_uri()}!/Top of Personal Folders"
    assert [i.uri for i in items] == [
        f"{base}/Inbox/2097188",
        f"{base}/Inbox/2097188/counts.csv",
        f"{base}/Inbox/2097188/counts-2.csv",
        f"{base}/Inbox/2097188/attachment-2",
        f"{base}/Sent Items/2097220",
    ]
    message = items[0]
    assert message.metadata["media_type"] == "message/rfc822"
    assert message.metadata["children_expanded"] is True
    assert message.metadata["fields"]["x_folder_paths"] == [
        "archive.pst/Top of Personal Folders/Inbox"
    ]
    parsed = email.message_from_bytes(
        Path(message.metadata["path"]).read_bytes(), policy=email.policy.default
    )
    assert parsed["Message-ID"] == "<survey-1@example.org>"
    assert parsed["Subject"] == "Heron survey results"  # unfolded
    assert "X-Mailer" not in parsed
    plain, markup = parsed.get_body(("plain",)), parsed.get_body(("html",))
    assert plain is not None and markup is not None
    assert plain.get_content().strip() == "Twelve herons on the lagoon."
    assert "<b>herons</b>" in markup.get_content()
    assert message.modified_at is None or message.modified_at.year == 2026
    attachment = items[1]
    assert attachment.metadata["parent_uri"] == message.uri
    assert Path(attachment.metadata["path"]).read_bytes() == b"species,count\nheron,12\n"
    reply = email.message_from_bytes(Path(items[-1].metadata["path"]).read_bytes())
    assert reply["From"] == "Pat Field" and "2026" in str(reply["Date"])
    payload = reply.get_payload(decode=True)
    assert isinstance(payload, bytes) and "Thanks" in payload.decode("utf-8")
    # the message libpff could not read is reported: nothing is deleted after this run
    assert len(connector.report.unchecked) == 1 and "Inbox#1" in connector.report.unchecked[0]


def test_without_attachments_and_with_a_message_limit(tmp_path: Path, fake_pypff: None) -> None:
    (tmp_path / "archive.pst").write_bytes(b"!BDN fake")
    plain = list(PstConnector().discover(source(tmp_path, attachments=False), None))
    assert [i.metadata["media_type"] for i in plain] == ["message/rfc822"] * 2
    assert plain[0].metadata["children_expanded"] is False
    capped = PstConnector()
    assert len(list(capped.discover(source(tmp_path, max_messages=1), None))) == 5  # + 4 files
    assert capped.report.truncated


def test_an_unreadable_pst_is_reported(tmp_path: Path, fake_pypff: None) -> None:
    (tmp_path / "broken.ost").write_bytes(b"BROKEN")
    connector = PstConnector()
    assert list(connector.discover(source(tmp_path), None)) == []
    assert "broken.ost" in connector.report.unchecked[0]


def test_without_the_extra_the_run_fails_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "pypff", None)  # import fails
    with pytest.raises(MissingExtra, match="pst` extra"):
        list(PstConnector().discover(source(tmp_path), None))


def test_the_real_binding_has_the_interface_the_connector_uses(tmp_path: Path) -> None:
    pypff = pytest.importorskip("pypff", reason="the LGPL `pst` extra is not installed")
    for cls, names in {
        pypff.file: ("open", "get_root_folder", "close"),
        pypff.folder: ("name", "number_of_sub_messages", "get_sub_message"),
        pypff.message: (
            "identifier",
            "subject",
            "sender_name",
            "transport_headers",
            "plain_text_body",
            "html_body",
            "delivery_time",
            "number_of_attachments",
            "get_attachment",
        ),
        pypff.attachment: ("long_filename", "get_size", "seek_offset", "read_buffer"),
    }.items():
        assert all(hasattr(cls, n) for n in names), cls
    (tmp_path / "not-a.pst").write_bytes(b"this is not an Outlook data file" * 100)
    connector = PstConnector()
    assert list(connector.discover(source(tmp_path), None)) == []
    assert "not-a.pst" in connector.report.unchecked[0]
    assert pst_module._pypff() is pypff
