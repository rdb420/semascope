"""Sources stay inside their roots and off private addresses; S3 credentials stay out of items."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from oss_core.contracts import SourceConfig, SourceItem
from oss_pipeline.connectors import guard
from oss_pipeline.connectors.filesystem import FilesystemConnector
from oss_pipeline.connectors.guard import SourceNotAllowed

FAKE_SECRET = "s3cr3t"  # noqa: S105 - a test value


def _src(connector: str, **settings: Any) -> SourceConfig:
    return SourceConfig(id="s", collection="c", connector=connector, settings=settings)


def test_paths_resolve_through_symlinks_before_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "import"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (root / "ok.txt").write_text("fine")
    (root / "link.txt").symlink_to(outside / "secret.txt")
    (root / "dir").symlink_to(outside)
    monkeypatch.setenv("SEMASCOPE_SOURCE_ROOTS", str(root))

    with pytest.raises(SourceNotAllowed):
        guard.check_source("filesystem", {"path": str(root / "dir")}, "c")
    with pytest.raises(SourceNotAllowed):
        guard.check_source("mail", {"path": str(root / ".." / "outside")}, "c")
    with pytest.raises(SourceNotAllowed):
        guard.check_source("table", {}, "c")
    guard.check_source("filesystem", {"path": str(root)}, "c")

    items = list(FilesystemConnector().discover(_src("filesystem", path=str(root)), None))
    assert [Path(i.metadata["path"]).name for i in items] == ["ok.txt"]
    items = list(
        FilesystemConnector().discover(
            _src("filesystem", path=str(root), follow_symlinks=True), None
        )
    )
    assert [Path(i.metadata["path"]).name for i in items] == ["ok.txt"]
    with pytest.raises(SourceNotAllowed):
        FilesystemConnector().fetch(
            SourceItem(uri="file:///x", metadata={"path": str(root / "link.txt")})
        )


def test_every_file_connector_refuses_a_path_outside_the_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_pipeline.registry import connectors

    monkeypatch.setenv("SEMASCOPE_SOURCE_ROOTS", str(tmp_path / "import"))
    monkeypatch.setenv("SEMASCOPE_DATA_DIR", str(tmp_path / "data"))
    for name in guard.PATH_CONNECTORS:
        with pytest.raises(SourceNotAllowed):
            list(connectors()[name].discover(_src(name, path=str(tmp_path)), None))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8983/solr",
        "http://10.1.2.3/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "file:///etc/passwd",
    ],
)
def test_private_and_odd_addresses_are_refused(url: str) -> None:
    with pytest.raises(SourceNotAllowed):
        guard.refuse_private(url)


def test_operator_can_allow_named_private_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "10.0.0.0/8, minio")
    guard.refuse_private("http://10.1.2.3:9000/")
    guard.refuse_private("http://minio:9000/")
    with pytest.raises(SourceNotAllowed):
        guard.refuse_private("http://127.0.0.1/")


def test_redirects_to_private_addresses_are_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})

    with guard.guarded_client(transport=httpx.MockTransport(handler), follow_redirects=True) as c:
        with pytest.raises(SourceNotAllowed):
            c.get("http://93.184.216.34/start")


def test_bodies_over_the_cap_are_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000)

    with guard.guarded_client(transport=httpx.MockTransport(handler)) as c:
        resp, body = guard.get_capped(c, "http://93.184.216.34/ok", 10000)
        assert len(body) == 5000
        with pytest.raises(guard.TooLarge):
            guard.get_capped(c, "http://93.184.216.34/big", 1000)


def test_sparql_endpoint_on_a_private_address_is_refused() -> None:
    from oss_pipeline.connectors.rdf import RdfConnector

    with pytest.raises(SourceNotAllowed):
        list(RdfConnector().discover(_src("rdf", endpoint="http://127.0.0.1:7200/sparql"), None))


def test_s3_items_carry_no_credentials_and_fetch_reads_the_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from oss_pipeline.connectors import s3

    made: list[dict[str, Any]] = []

    class Paginator:
        def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [{"Contents": [{"Key": "a/b.txt", "Size": 3, "ETag": '"e"'}]}]

    class Client:
        def get_paginator(self, name: str) -> Paginator:
            return Paginator()

        def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
            class Body:
                def iter_chunks(self, size: int) -> list[bytes]:
                    return [b"abc"]

                def close(self) -> None:
                    pass

            return {"Body": Body(), "ContentLength": 3}

    def fake_client(settings: dict[str, Any]) -> Client:
        made.append(dict(settings))
        return Client()

    monkeypatch.setattr(s3, "_client", fake_client)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    source = _src("s3", bucket="bkt", access_key="AKIA", secret_key=FAKE_SECRET, region="r")
    items = list(s3.S3Connector().discover(source, None))
    dumped = items[0].model_dump_json()
    assert FAKE_SECRET not in dumped and "AKIA" not in dumped
    fetched = s3.S3Connector().fetch(SourceItem.model_validate_json(dumped), source=source)
    assert fetched.path and Path(fetched.path).read_bytes() == b"abc"
    assert made[-1]["secret_key"] == FAKE_SECRET


def test_s3_endpoint_on_a_private_address_is_refused() -> None:
    from oss_pipeline.connectors import s3

    with pytest.raises(SourceNotAllowed):
        s3.S3Connector()._for({"endpoint_url": "http://127.0.0.1:9000"})


@pytest.mark.parametrize(
    "url",
    [
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://100.127.255.254/",
        "http://192.0.2.10/",  # documentation
        "http://198.18.0.1/",  # benchmarking
        "http://[64:ff9b::7f00:1]/",  # NAT64 of 127.0.0.1
        "http://[64:ff9b::a00:1]/",  # NAT64 of 10.0.0.1
        "http://[2002:7f00:1::]/",  # 6to4 of 127.0.0.1
        "http://[2002:a9fe:a9fe::]/",  # 6to4 of 169.254.169.254
        "http://[2001:0:4136:e378:8000:63bf:3fff:fdd2]/",  # Teredo
        "http://[::ffff:10.0.0.1]/",  # IPv4-mapped private
        "http://[::ffff:169.254.169.254]/",
        "http://[fd00::1]/",  # unique local
        "http://[fe80::1]/",  # link-local
        "http://[2001:db8::1]/",  # documentation
        "http://224.0.0.1/",  # multicast
        "http://233.252.0.1/",  # global-scope multicast
    ],
)
def test_only_globally_routable_addresses_are_reached(url: str) -> None:
    with pytest.raises(SourceNotAllowed):
        guard.refuse_private(url)


@pytest.mark.parametrize("address", ["8.8.8.8", "2001:4860:4860::8888", "64:ff9b::808:808"])
def test_global_addresses_and_their_ipv6_forms_pass(address: str) -> None:
    import ipaddress

    assert guard._is_public(ipaddress.ip_address(address))
    if "." in address:
        assert guard._is_public(ipaddress.ip_address(f"::ffff:{address}"))


def test_max_bytes_has_a_ceiling() -> None:
    ceiling = guard.MAX_BYTES_CEILING
    assert guard.byte_cap({}, 1000) == 1000
    assert guard.byte_cap({"max_bytes": 5}, 1000) == 5
    assert guard.byte_cap({"max_bytes": ceiling * 50}, 1000) == ceiling  # clamped at run time
    for bad in (0, -1, "lots", None, True):
        with pytest.raises(SourceNotAllowed):
            guard.byte_cap({"max_bytes": bad}, 1000)
    with pytest.raises(SourceNotAllowed, match="ceiling"):
        guard.check_source("web", {"urls": ["https://example.org/"], "max_bytes": ceiling + 1}, "c")
    guard.check_source("web", {"urls": ["https://example.org/"], "max_bytes": ceiling}, "c")
