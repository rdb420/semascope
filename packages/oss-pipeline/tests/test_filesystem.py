from pathlib import Path

import pytest

from oss_core.contracts import SourceConfig
from oss_pipeline.connectors.filesystem import FilesystemConnector


def test_discover_respects_globs_and_hashes(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.md").write_text("world")
    (tmp_path / ".hidden").write_text("no")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.txt").write_text("deep")
    (tmp_path / "empty.txt").write_text("")
    src = SourceConfig(
        id="s",
        collection="c",
        connector="filesystem",
        settings={"path": str(tmp_path), "include": ["*.txt"]},
    )
    items = list(FilesystemConnector().discover(src, None))
    names = sorted(Path(i.metadata["path"]).name for i in items)
    assert names == ["a.txt", "c.txt"]
    assert all(len(i.content_hash or "") == 64 and i.size for i in items)
    fetched = FilesystemConnector().fetch(items[0])
    assert fetched.path and Path(fetched.path).exists()


def test_upload_connector_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from oss_pipeline.connectors.upload import UploadConnector, store_upload

    monkeypatch.setenv("SEMASCOPE_DATA_DIR", str(tmp_path))

    dest = store_upload(tmp_path, "c", "../evil.txt", b"hello")
    assert dest.name == "evil.txt" and dest.parent.parent.name == "c"
    src = SourceConfig(
        id="s",
        collection="c",
        connector="upload",
        settings={"path": str(tmp_path / "uploads" / "c")},
    )
    items = list(UploadConnector().discover(src, None))
    assert (
        len(items) == 1
        and items[0].uri.startswith("upload://c/")
        and items[0].content_hash == dest.parent.name
    )
