"""Connectors never pass over what they could not read, and archives cannot exhaust a worker.

A full source run deletes what its connector did not yield, so each connector reports
what it could not read (`RunReport.unchecked`) or stopped short of (`truncated`), and
fails the run when it could read nothing (SEMA-010, SEMA-064). Archives are expanded
within byte, ratio, member and depth budgets counted on what is really decompressed
(SEMA-017). Work files go to the shared data volume (SEMA-060); mail identities do not
collide (SEMA-059); unchanged files are not read again (SEMA-061); items carry folder and
extension path fields.
"""

from __future__ import annotations

import io
import mailbox
import os
import tarfile
import threading
import zipfile
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx
import pytest

from oss_core.contracts import SourceConfig, SourceItem
from oss_pipeline.connectors import base
from oss_pipeline.connectors.archive import ArchiveConnector
from oss_pipeline.connectors.feed import FeedConnector
from oss_pipeline.connectors.filesystem import FilesystemConnector
from oss_pipeline.connectors.mail import MailConnector
from oss_pipeline.connectors.web import WebConnector

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "tests" / "integration" / "stack"


def _src(connector: str, **settings: Any) -> SourceConfig:
    return SourceConfig(id="7", collection="c", connector=connector, settings=settings)


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("fixture_server", STACK / "fixture_server.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fixture_server"] = module
    spec.loader.exec_module(module)
    server = module.serve(STACK / "web", host="127.0.0.1", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "127.0.0.1/32")


def _serve(site: str, path: str, body: str, ctype: str = "text/html") -> None:
    httpx.put(f"{site}/files{path}", content=body, headers={"Content-Type": ctype})


# --- web (SEMA-010, SEMA-064) --------------------------------------------------------------


def test_a_page_that_answers_503_is_reported_not_passed_over(site: str, loopback: None) -> None:
    connector = WebConnector()
    urls = [f"{site}/page-a.html", f"{site}/status/503", f"{site}/status/404"]
    found = list(connector.discover(_src("web", urls=urls, delay_seconds=0), None))
    assert [i.uri for i in found] == [f"{site}/page-a.html"]
    # 503 is "try again", so the run deletes nothing; 404 is "gone", so it may
    assert len(connector.report.unchecked) == 1 and "503" in connector.report.unchecked[0]
    assert not connector.report.complete


def test_a_site_that_is_down_fails_the_run(site: str, loopback: None) -> None:
    connector = WebConnector()
    settings = {"urls": [f"{site}/status/503"], "mode": "crawl", "delay_seconds": 0}
    with pytest.raises(ConnectionError, match="no page could be read"):
        list(connector.discover(_src("web", **settings), None))


def test_a_crawl_stopped_at_max_pages_is_truncated(site: str, loopback: None) -> None:
    connector = WebConnector()
    settings = {"urls": [f"{site}/index.html"], "mode": "crawl", "max_pages": 2}
    assert len(list(connector.discover(_src("web", delay_seconds=0, **settings), None))) == 2
    assert connector.report.truncated


def test_a_bad_link_in_a_sitemap_does_not_end_the_run(site: str, loopback: None) -> None:
    _serve(
        site,
        "/sitemap-bad.xml",
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>http://no-such-host.invalid/x.html</loc></url>"
        "<url><loc>@BASE@/page-b.html</loc></url></urlset>",
        "application/xml",
    )
    connector = WebConnector()
    settings = {"urls": [f"{site}/sitemap-bad.xml"], "mode": "sitemap", "delay_seconds": 0}
    found = list(connector.discover(_src("web", **settings), None))
    assert [i.uri for i in found] == [f"{site}/page-b.html"]
    assert connector.report.unchecked and "no-such-host" in connector.report.unchecked[0]


def test_a_sitemap_that_lists_itself_is_read_once(site: str, loopback: None) -> None:
    _serve(
        site,
        "/sitemap-loop.xml",
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<sitemap><loc>@BASE@/sitemap-loop.xml</loc></sitemap>"
        "<sitemap><loc>@BASE@/sitemap-pages.xml</loc></sitemap></sitemapindex>",
        "application/xml",
    )
    connector = WebConnector()
    settings = {"urls": [f"{site}/sitemap-loop.xml"], "mode": "sitemap", "delay_seconds": 0}
    found = {i.uri for i in connector.discover(_src("web", **settings), None)}
    assert f"{site}/page-a.html" in found and connector.report.complete


def test_an_unreadable_first_sitemap_fails_the_run(site: str, loopback: None) -> None:
    settings = {"urls": [f"{site}/status/502"], "mode": "sitemap", "delay_seconds": 0}
    with pytest.raises(ConnectionError, match="sitemap"):
        list(WebConnector().discover(_src("web", **settings), None))


def test_web_pages_land_in_the_source_work_area_with_path_fields(site: str, loopback: None) -> None:
    (item,) = WebConnector().discover(
        _src("web", urls=[f"{site}/deep/level1.html"], delay_seconds=0), None
    )
    path = Path(item.metadata["path"])
    assert path.is_relative_to(base.work_root() / "c" / "7" / "web")
    assert item.metadata["fields"] == {
        "x_folder_paths": [f"{site.removeprefix('http://')}/deep"],
        "x_extension_s": "html",
    }


def test_a_feed_entry_link_that_fails_is_reported(site: str, loopback: None) -> None:
    _serve(
        site,
        "/feeds/flaky.xml",
        '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
        "<item><title>a</title><link>@BASE@/page-a.html</link></item>"
        "<item><title>b</title><link>@BASE@/status/500</link></item>"
        "</channel></rss>",
        "application/rss+xml",
    )
    connector = FeedConnector()
    settings = {"urls": [f"{site}/feeds/flaky.xml"], "fetch_links": True}
    found = list(connector.discover(_src("feed", **settings), None))
    assert [i.uri for i in found] == [f"{site}/page-a.html"]
    assert connector.report.unchecked and connector.deletes_missing is False


# --- filesystem ----------------------------------------------------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every folder")
def test_a_folder_that_cannot_be_listed_is_reported(tmp_path: Path) -> None:
    (tmp_path / "open").mkdir()
    (tmp_path / "open" / "a.txt").write_text("a")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "b.txt").write_text("b")
    locked.chmod(0)
    try:
        connector = FilesystemConnector()
        found = list(connector.discover(_src("filesystem", path=str(tmp_path)), None))
    finally:
        locked.chmod(0o755)
    assert [Path(i.metadata["path"]).name for i in found] == ["a.txt"]
    assert connector.report.unchecked and "locked" in connector.report.unchecked[0]


def test_an_unchanged_file_is_not_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_pipeline.connectors import filesystem

    folder = tmp_path / "docs"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "Report.PDF").write_bytes(b"%PDF-1.4 report")
    first = list(FilesystemConnector().discover(_src("filesystem", path=str(folder)), None))
    item = first[0]
    assert item.metadata["fields"] == {"x_folder_paths": ["docs/sub"], "x_extension_s": "pdf"}
    reads: list[Path] = []
    real = filesystem.sha256_file

    def counting(path: Path) -> str:
        reads.append(path)
        return real(path)

    monkeypatch.setattr(filesystem, "sha256_file", counting)
    known: dict[str, Any] = {item.uri: (item.size, item.modified_at, item.content_hash)}
    again = list(
        FilesystemConnector().discover(_src("filesystem", path=str(folder)), None, known.get)
    )
    assert reads == [] and again[0].content_hash == item.content_hash
    os.utime(folder / "sub" / "Report.PDF", (1, 1))  # touched: read again
    list(FilesystemConnector().discover(_src("filesystem", path=str(folder)), None, known.get))
    assert len(reads) == 1


# --- archives (SEMA-017) -------------------------------------------------------------------


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _expand(path: Path, **limits: Any) -> tuple[list[SourceItem], ArchiveConnector]:
    connector = ArchiveConnector()
    return list(connector.discover(_src("archive", path=str(path), **limits), None)), connector


def test_the_ratio_budget_covers_every_nesting_level(tmp_path: Path) -> None:
    """Each level within the ratio of its own size is still a bomb when nested: the budget
    is the outer archive's size times the ratio, for everything expanded from it."""

    zeros = b"\0" * 2_000_000  # deflates to about 2 kB
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for n in range(5):
            zf.writestr(f"z{n}.bin", zeros)
    outer = _zip(tmp_path / "outer.zip", {"note.txt": b"hello", "inner.zip": inner.getvalue()})
    size = outer.stat().st_size
    items, _ = _expand(outer, max_ratio=1000)
    expanded = sum(i.size or 0 for i in items)
    assert expanded <= size * 1000
    assert 0 < len(items) < 6  # the note, and the members the budget allowed


def test_max_total_bytes_stops_an_archive(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "a.zip", {f"m{n}.txt": b"x" * 1000 for n in range(10)})
    items, _ = _expand(archive, max_total_bytes=3500)
    assert len(items) == 3


def test_members_over_the_size_or_ratio_limit_are_skipped(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "a.zip", {"big.bin": b"\0" * 100_000, "ok.txt": b"fine"})
    items, _ = _expand(archive, max_ratio=10)  # big.bin claims a ratio near 1000
    assert [i.metadata["member"] for i in items] == ["ok.txt"]
    items, _ = _expand(archive, max_member_bytes=50_000)
    assert [i.metadata["member"] for i in items] == ["ok.txt"]


def test_the_member_limit_counts_nested_members(tmp_path: Path) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        for n in range(5):
            zf.writestr(f"i{n}.txt", f"inner {n}")
    archive = _zip(tmp_path / "a.zip", {"inner.zip": inner.getvalue(), "z.txt": b"last"})
    items, _ = _expand(archive, max_members=4)
    assert len(items) == 3  # the nested archive counts as one, then three of its members


def test_members_are_streamed_to_disk_and_nested_copies_removed(tmp_path: Path) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep/note.txt", "inner text")
    folder = tmp_path / "in"
    folder.mkdir()
    _zip(folder / "outer.zip", {"inner.zip": inner.getvalue()})
    with tarfile.open(folder / "t.tar.gz", "w:gz") as tf:
        data = b"tar member"
        info = tarfile.TarInfo("docs/m.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    items, connector = _expand(folder)
    work = base.work_root() / "c" / "7" / "archive"
    left = sorted(p.name for p in work.iterdir())
    assert left == sorted(Path(i.metadata["path"]).name for i in items)  # no copies, no parts
    fields = {i.metadata["member"]: i.metadata["fields"] for i in items}
    assert fields["deep/note.txt"]["x_folder_paths"] == ["in/outer.zip/inner.zip/deep"]
    assert fields["docs/m.txt"]["x_folder_paths"] == ["in/t.tar.gz/docs"]
    assert connector.report.complete


def test_a_corrupt_archive_is_reported(tmp_path: Path) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "broken.zip").write_bytes(b"PK\x03\x04 not really a zip")
    _zip(folder / "fine.zip", {"a.txt": b"a"})
    items, connector = _expand(folder)
    assert [i.metadata["member"] for i in items] == ["a.txt"]
    assert connector.report.unchecked and "broken.zip" in connector.report.unchecked[0]


# --- mail (SEMA-059) -----------------------------------------------------------------------


def _message(body: str, message_id: str = "<same@example.org>") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "a@example.org"
    msg["Subject"] = "s"
    msg["Message-ID"] = message_id
    msg.set_content(body)
    return msg


def test_two_messages_sharing_a_message_id_are_two_documents(tmp_path: Path) -> None:
    box = mailbox.mbox(str(tmp_path / "in.mbox"))
    box.add(_message("first"))
    box.add(_message("second, forged id"))
    box.flush()
    items = list(MailConnector().discover(_src("mail", path=str(tmp_path)), None))
    uris = [i.uri for i in items]
    assert len(set(uris)) == 2 and all(u.startswith("mail:same@example.org#") for u in uris)


def test_a_flag_set_by_a_mail_client_keeps_the_identity(tmp_path: Path) -> None:
    (tmp_path / "a.eml").write_bytes(_message("body").as_bytes())
    first = [i.uri for i in MailConnector().discover(_src("mail", path=str(tmp_path)), None)]
    read = _message("body")
    read["Status"] = "RO"
    (tmp_path / "a.eml").write_bytes(read.as_bytes())
    again = [i.uri for i in MailConnector().discover(_src("mail", path=str(tmp_path)), None)]
    assert first == again


def test_attachments_with_the_same_name_do_not_collide(tmp_path: Path) -> None:
    msg = _message("see attached")
    for data in (b"one", b"two"):
        msg.add_attachment(data, maintype="text", subtype="plain", filename="notes.txt")
    (tmp_path / "a.eml").write_bytes(msg.as_bytes())
    items = list(MailConnector().discover(_src("mail", path=str(tmp_path)), None))
    names = [i.uri.rsplit("/", 1)[-1] for i in items if "parent_uri" in i.metadata]
    assert names == ["notes.txt", "notes-2.txt"]


def test_files_without_a_suffix_are_mbox_only_when_they_look_like_one(tmp_path: Path) -> None:
    (tmp_path / "README").write_text("just some notes\nFrom here on, nothing\n")
    box = mailbox.mbox(str(tmp_path / "Inbox"))  # Thunderbird's name for an mbox file
    box.add(_message("in the inbox", "<inbox@example.org>"))
    box.flush()
    items = list(MailConnector().discover(_src("mail", path=str(tmp_path)), None))
    assert [i.uri.split("#")[0] for i in items] == ["mail:inbox@example.org"]


# --- path fields ---------------------------------------------------------------------------


def test_path_fields_are_a_hierarchy_and_an_extension() -> None:
    from oss_pipeline.stages.index import expand_paths

    fields = base.path_fields(["import", "legal", "2024"], "Lease.Final.DOCX")
    assert fields == {"x_folder_paths": ["import/legal/2024"], "x_extension_s": "docx"}
    assert expand_paths(fields["x_folder_paths"]) == ["import", "import/legal", "import/legal/2024"]
    assert base.path_fields([], "README") == {}
    assert base.path_fields(["a/b", "..", ""], ".bashrc") == {"x_folder_paths": ["a_b"]}
