"""The fetch stage stores what it read, under the SHA-256 of what it read (SEMA-061)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.stages.fetch import FetchStage


def test_the_blob_is_named_by_what_was_read(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    src.write_bytes(b"changed after discovery")
    blobs = tmp_path / "blobs"
    stale = hashlib.sha256(b"what discovery read").hexdigest()
    doc = WorkingDocument(
        id="d",
        collection="c",
        source_id="1",
        uri="u",
        content_hash=stale,
        fields={"local_path": str(src)},
    )
    ctx = StageContext(collection="c", profile="fast", settings={"blob_dir": str(blobs)})
    out = FetchStage().run(doc, ctx)
    actual = hashlib.sha256(b"changed after discovery").hexdigest()
    assert out.content_hash == actual
    assert Path(out.fields["blob_path"]).name == f"{actual}.txt"
    assert Path(out.fields["blob_path"]).read_bytes() == b"changed after discovery"
    assert not list((blobs / ".incoming").iterdir())


def test_an_s3_etag_is_never_a_blob_name(tmp_path: Path) -> None:
    src = tmp_path / "obj.pdf"
    src.write_bytes(b"%PDF object")
    doc = WorkingDocument(
        id="d",
        collection="c",
        source_id="1",
        uri="s3://b/obj.pdf",
        content_hash="9e107d9d372bb6826bd81d3542a419d6-2",  # a multipart ETag
        fields={"local_path": str(src)},
    )
    ctx = StageContext(collection="c", profile="fast", settings={"blob_dir": str(tmp_path / "b")})
    out = FetchStage().run(doc, ctx)
    digest = hashlib.sha256(b"%PDF object").hexdigest()
    assert Path(out.fields["blob_path"]).name == f"{digest}.pdf"
    assert out.content_hash == "9e107d9d372bb6826bd81d3542a419d6-2"  # still the change token


def test_a_stored_blob_is_reused_without_the_work_file(tmp_path: Path) -> None:
    digest = hashlib.sha256(b"same bytes").hexdigest()
    blob = tmp_path / "b" / digest[:2] / f"{digest}.html"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"same bytes")
    os.utime(blob, (1, 1))
    doc = WorkingDocument(
        id="d",
        collection="c",
        source_id="1",
        uri="u",
        content_hash=digest,
        fields={"local_path": str(tmp_path / "gone" / "page.html")},  # removed meanwhile
    )
    ctx = StageContext(collection="c", profile="fast", settings={"blob_dir": str(tmp_path / "b")})
    out = FetchStage().run(doc, ctx)
    assert out.fields["blob_path"] == str(blob)
    assert blob.stat().st_mtime > 1  # touched: the blob sweep sees it in use
