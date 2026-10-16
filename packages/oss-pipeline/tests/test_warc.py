"""WARC connector: one document per archived page, its original URL and capture date."""

from __future__ import annotations

import gzip
import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from oss_core.contracts import SourceConfig
from oss_pipeline.connectors.warc import WarcConnector
from oss_pipeline.registry import connectors


def _response(
    writer: WARCWriter,
    uri: str,
    body: bytes,
    *,
    date: str,
    status: str = "200 OK",
    ctype: str = "text/html; charset=utf-8",
    gzip_body: bool = False,
) -> None:
    headers = [("Content-Type", ctype)]
    if gzip_body:
        body = gzip.compress(body)
        headers.append(("Content-Encoding", "gzip"))
    http = StatusAndHeaders(status, headers, protocol="HTTP/1.1")
    record = writer.create_warc_record(
        uri,
        "response",
        payload=io.BytesIO(body),
        http_headers=http,
        warc_headers_dict={"WARC-Date": date},
    )
    writer.write_record(record)


def _warc(path: Path, *, gz: bool = True) -> None:
    with path.open("wb") as fh:
        writer = WARCWriter(fh, gzip=gz)
        writer.write_record(writer.create_warcinfo_record(path.name, {"software": "tests"}))
        _response(
            writer,
            "https://birds.example/wetlands/egret.html",
            b"<html><title>Egret</title><body>An old egret page.</body></html>",
            date="2026-01-02T03:04:05Z",
        )
        _response(
            writer,
            "https://birds.example/wetlands/egret.html",
            b"<html><title>Egret</title><body>A great egret wades.</body></html>",
            date="2026-03-04T05:06:07Z",
        )
        _response(
            writer,
            "https://birds.example/",
            b"<html><body>Bird index</body></html>",
            date="2026-03-04T05:06:08Z",
            gzip_body=True,
        )
        _response(
            writer,
            "https://birds.example/gone",
            b"not found",
            date="2026-03-04T05:06:09Z",
            status="404 Not Found",
        )
        _response(
            writer,
            "https://birds.example/moved",
            b"",
            date="2026-03-04T05:06:10Z",
            status="301 Moved Permanently",
        )
        request = writer.create_warc_record(
            "https://birds.example/", "request", payload=io.BytesIO(b"GET / HTTP/1.1\r\n\r\n")
        )
        writer.write_record(request)
        resource = writer.create_warc_record(
            "https://birds.example/data/counts.csv",
            "resource",
            payload=io.BytesIO(b"species,count\negret,3\n"),
            warc_content_type="text/csv",
            warc_headers_dict={"WARC-Date": "2026-03-05T00:00:00Z"},
        )
        writer.write_record(resource)


def _source(path: Path, **settings: object) -> SourceConfig:
    return SourceConfig(
        id="w", collection="c", connector="warc", settings={"path": str(path), **settings}
    )


def test_warc_is_a_registered_connector() -> None:
    assert "warc" in connectors()


@pytest.mark.parametrize("gz", [True, False])
def test_one_document_per_archived_page_with_its_url_and_date(tmp_path: Path, gz: bool) -> None:
    archive = tmp_path / ("crawl.warc.gz" if gz else "crawl.warc")
    _warc(archive, gz=gz)
    connector = WarcConnector()
    items = {i.uri: i for i in connector.discover(_source(tmp_path), None)}
    assert set(items) == {
        "https://birds.example/wetlands/egret.html",
        "https://birds.example/",
        "https://birds.example/data/counts.csv",
    }
    egret = items["https://birds.example/wetlands/egret.html"]
    assert egret.modified_at == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)  # the latest capture
    assert b"great egret" in Path(egret.metadata["path"]).read_bytes()
    assert egret.metadata["media_type"] == "text/html"
    assert egret.metadata["parent_uri"] == archive.as_uri()
    assert egret.metadata["fields"] == {
        "x_folder_paths": ["birds.example/wetlands"],
        "x_extension_s": "html",
    }
    index = Path(items["https://birds.example/"].metadata["path"]).read_bytes()
    assert index == b"<html><body>Bird index</body></html>"  # gzip content encoding undone
    csv = items["https://birds.example/data/counts.csv"]
    assert csv.metadata["media_type"] == "text/csv"
    assert connector.report.complete


def test_limits_skip_large_payloads_and_stop_the_run(tmp_path: Path) -> None:
    _warc(tmp_path / "crawl.warc.gz")
    small = WarcConnector()
    uris = {i.uri for i in small.discover(_source(tmp_path, max_record_bytes=40), None)}
    assert uris == {"https://birds.example/data/counts.csv", "https://birds.example/"}
    assert small.report.complete  # a skipped large page is a limit, not an unreadable file
    capped = WarcConnector()
    assert len(list(capped.discover(_source(tmp_path, max_records=1), None))) == 1
    assert capped.report.truncated  # nothing is deleted by absence after this run


def test_an_unreadable_archive_is_reported(tmp_path: Path) -> None:
    _warc(tmp_path / "good.warc.gz")
    (tmp_path / "broken.warc.gz").write_bytes(b"\x1f\x8b\x08 this is not gzip at all")
    connector = WarcConnector()
    uris = {i.uri for i in connector.discover(_source(tmp_path), None)}
    assert "https://birds.example/" in uris
    assert any("broken.warc.gz" in u for u in connector.report.unchecked)


def test_a_path_outside_the_source_roots_is_refused(tmp_path: Path) -> None:
    from oss_pipeline.connectors.guard import SourceNotAllowed

    with pytest.raises(SourceNotAllowed):
        list(WarcConnector().discover(_source(Path("/etc")), None))
