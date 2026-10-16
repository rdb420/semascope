import io
import mailbox
import tarfile
import zipfile
from email.message import EmailMessage
from pathlib import Path

from oss_core.contracts import SourceConfig
from oss_pipeline.connectors.archive import ArchiveConnector
from oss_pipeline.connectors.mail import MailConnector
from oss_pipeline.connectors.table import TableConnector
from oss_pipeline.registry import connectors


def test_registry_lists_every_builtin_connector() -> None:
    assert {"filesystem", "web", "upload", "feed", "mail", "archive", "table", "s3", "rdf"} <= set(
        connectors()
    )


def test_archive_members_and_nested_archives(tmp_path: Path) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep/note.txt", "inner text")
    with zipfile.ZipFile(tmp_path / "outer.zip", "w") as zf:
        zf.writestr("a.txt", "hello")
        zf.writestr("inner.zip", inner.getvalue())
        zf.writestr(".hidden", "no")
    with tarfile.open(tmp_path / "t.tar.gz", "w:gz") as tf:
        data = b"tar member"
        info = tarfile.TarInfo("m.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    src = SourceConfig(
        id="s", collection="c", connector="archive", settings={"path": str(tmp_path)}
    )
    items = list(ArchiveConnector().discover(src, None))
    uris = sorted(i.uri.split("!/", 1)[1] for i in items)
    assert uris == ["a.txt", "inner.zip!/deep/note.txt", "m.txt"]
    assert all(Path(i.metadata["path"]).exists() and i.metadata["parent_uri"] for i in items)


def test_mail_messages_and_attachments(tmp_path: Path) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Hello"
    msg["From"] = "a@example.org"
    msg["To"] = "b@example.org"
    msg["Date"] = "Wed, 15 Oct 2026 10:00:00 +1000"
    msg["Message-ID"] = "<m1@example.org>"
    msg.set_content("body text")
    msg.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="doc.pdf")
    box = mailbox.mbox(str(tmp_path / "in.mbox"))
    box.add(msg)
    box.flush()
    (tmp_path / "single.eml").write_bytes(msg.as_bytes())
    src = SourceConfig(id="s", collection="c", connector="mail", settings={"path": str(tmp_path)})
    items = list(MailConnector().discover(src, None))
    kinds = sorted((i.uri, i.metadata.get("media_type")) for i in items)
    assert ("mail:m1@example.org", "message/rfc822") in kinds
    assert ("mail:m1@example.org/doc.pdf", "application/pdf") in kinds
    assert len(items) == 4  # same message from the mbox and the eml, each with its attachment
    assert (
        items[0].modified_at is not None and items[0].modified_at.hour == 0
    )  # 10:00 +10:00 is 00:00 UTC


def test_table_rows_become_documents(tmp_path: Path) -> None:
    (tmp_path / "people.csv").write_text(
        "id,name,role,notes\n1,Ann,Owner,Likes maps\n2,Bob,Tenant,\n", encoding="utf-8"
    )
    src = SourceConfig(
        id="s",
        collection="c",
        connector="table",
        settings={
            "path": str(tmp_path),
            "title_column": "name",
            "id_column": "id",
            "field_columns": {"role": "role_s"},
        },
    )
    items = list(TableConnector().discover(src, None))
    assert [i.uri.rsplit("=", 1)[1] for i in items] == ["1", "2"]
    assert items[0].metadata["title"] == "Ann" and items[0].metadata["fields"] == {
        "x_role_s": "Owner"
    }
    text = Path(items[0].metadata["path"]).read_text()
    assert "role: Owner" in text and "notes: Likes maps" in text


def test_rdf_subjects_become_documents(tmp_path: Path) -> None:
    from oss_pipeline.connectors.rdf import RdfConnector

    (tmp_path / "data.ttl").write_text(
        """@prefix ex: <https://e/> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        ex:troy a ex:Place ; rdfs:label "Troy" ; ex:region ex:anatolia .
        ex:odysseus a ex:Hero ; rdfs:label "Odysseus" ; ex:home ex:ithaca .
        """,
        encoding="utf-8",
    )
    src = SourceConfig(
        id="s",
        collection="c",
        connector="rdf",
        settings={"path": str(tmp_path), "types": ["https://e/Place"]},
    )
    items = list(RdfConnector().discover(src, None))
    assert [i.uri for i in items] == ["https://e/troy"]
    assert items[0].metadata["title"] == "Troy" and items[0].metadata["fields"] == {
        "x_subject_s": "https://e/troy"
    }
    assert "region: anatolia" in Path(items[0].metadata["path"]).read_text()
    assert (
        len(
            list(
                RdfConnector().discover(
                    SourceConfig(
                        id="s", collection="c", connector="rdf", settings={"path": str(tmp_path)}
                    ),
                    None,
                )
            )
        )
        == 2
    )


class _ContextServer:
    """A local HTTP server that serves a JSON-LD context and counts the requests it gets."""

    def __init__(self) -> None:
        import http.server
        import json
        import threading

        hits: list[str] = []
        body = json.dumps({"@context": {"name": "http://www.w3.org/2000/01/rdf-schema#label"}})

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the http.server API
                hits.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/ld+json")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args: object) -> None:
                pass

        self.hits = hits
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/context.jsonld"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def test_rdf_jsonld_files_never_fetch_a_remote_context(tmp_path: Path) -> None:
    import json

    import pytest

    from oss_pipeline.connectors.guard import SourceNotAllowed
    from oss_pipeline.connectors.rdf import RdfConnector

    server = _ContextServer()
    try:
        for n, context in enumerate(
            [server.url, [server.url], {"@import": server.url, "x": "https://e/x"}]
        ):
            folder = tmp_path / f"case{n}"
            folder.mkdir()
            (folder / "data.jsonld").write_text(
                json.dumps({"@context": context, "@id": "https://e/troy", "name": "Troy"}),
                encoding="utf-8",
            )
            src = SourceConfig(
                id="s", collection="c", connector="rdf", settings={"path": str(folder)}
            )
            with pytest.raises(SourceNotAllowed, match="remote JSON-LD contexts"):
                list(RdfConnector().discover(src, None))
        assert server.hits == []
    finally:
        server.server.shutdown()

    inline = tmp_path / "inline"
    inline.mkdir()
    (inline / "data.jsonld").write_text(
        json.dumps(
            {
                "@context": {"name": "http://www.w3.org/2000/01/rdf-schema#label"},
                "@id": "https://e/troy",
                "name": "Troy",
            }
        ),
        encoding="utf-8",
    )
    src = SourceConfig(id="s", collection="c", connector="rdf", settings={"path": str(inline)})
    (item,) = list(RdfConnector().discover(src, None))
    assert item.metadata["title"] == "Troy"
