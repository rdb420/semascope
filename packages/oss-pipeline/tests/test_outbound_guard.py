"""Every outbound client type against a local origin: DNS rebinding, redirects, caps, timeouts.

A `*.test` name resolves to a public address for its first N lookups and to 127.0.0.1
after that (outbound_fakes.Rebinding), for every N a run could need. Whatever N is, no
client type may reach the origin on 127.0.0.1: the guarded clients resolve once per
connection, check, and connect to the checked address. With the name allowed, the same
runs do reach the origin, through the pinned address, so the refusals are not vacuous.
"""

from __future__ import annotations

import ipaddress
import os
import ssl
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from outbound_fakes import OBJECT, Origin, Rebinding, origin_server, quietly

from oss_core.contracts import SourceConfig
from oss_pipeline.connectors import guard
from oss_pipeline.connectors.feed import FeedConnector
from oss_pipeline.connectors.guard import SourceNotAllowed, TooLarge
from oss_pipeline.connectors.rdf import RdfConnector
from oss_pipeline.connectors.s3 import S3Connector
from oss_pipeline.connectors.web import WebConnector, _Robots

CAP = 1024 * 1024
KEY = "AKIAEXAMPLE"
SECRET = "example-secret"  # noqa: S105 - a test value


@pytest.fixture(scope="module")
def origin() -> Iterator[Origin]:
    with origin_server() as server:
        yield server


@pytest.fixture(autouse=True)
def _fresh(origin: Origin, monkeypatch: pytest.MonkeyPatch) -> None:
    origin.reset()
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")  # no botocore retries with back-off
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


def _src(connector: str, **settings: Any) -> SourceConfig:
    return SourceConfig(id="s", collection="c", connector=connector, settings=settings)


# --- one runner per client type -------------------------------------------------------------


def web_page(base: str) -> Any:
    source = _src("web", urls=[f"{base}/page"], delay_seconds=0, max_bytes=CAP)
    return list(WebConnector().discover(source, None))


def robots_txt(base: str) -> Any:
    with guard.guarded_client(timeout=5.0, follow_redirects=True) as client:
        return _Robots("t", client).allowed(f"{base}/page")


def sitemap(base: str) -> Any:
    source = _src(
        "web", urls=[f"{base}/sitemap.xml"], mode="sitemap", delay_seconds=0, max_bytes=CAP
    )
    return list(WebConnector().discover(source, None))


def feed(base: str) -> Any:
    source = _src("feed", urls=[f"{base}/feed.xml"], fetch_links=True, max_bytes=CAP)
    return list(FeedConnector().discover(source, None))


def sparql(base: str) -> Any:
    source = _src("rdf", endpoint=f"{base}/sparql", max_bytes=CAP)
    return list(RdfConnector().discover(source, None))


def s3(base: str) -> Any:
    source = _src(
        "s3",
        bucket="bkt",
        endpoint_url=base,
        region="us-east-1",
        access_key=KEY,
        secret_key=SECRET,
        max_bytes=CAP,
    )
    connector = S3Connector()
    items = list(connector.discover(source, None))
    return [Path(str(connector.fetch(i, source=source).path)).read_bytes() for i in items]


RUNNERS: list[Callable[[str], Any]] = [web_page, robots_txt, sitemap, feed, sparql, s3]
IDS = [r.__name__ for r in RUNNERS]


# --- DNS rebinding ----------------------------------------------------------------------------


@pytest.mark.parametrize("public_answers", [0, 1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("run", RUNNERS, ids=IDS)
def test_a_name_that_rebinds_to_a_private_address_never_reaches_it(
    run: Callable[[str], Any],
    public_answers: int,
    origin: Origin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dns = Rebinding(monkeypatch, public_answers)
    quietly(lambda: run(origin.url("rebind.test")))
    assert origin.hits == []
    assert not dns.reached_private()
    assert all(guard._is_public(ipaddress.ip_address(a)) for a in dns.connects)


@pytest.mark.parametrize("run", RUNNERS, ids=IDS)
def test_the_same_runs_reach_an_allowed_host_through_the_pinned_address(
    run: Callable[[str], Any], origin: Origin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "allowed.test")
    dns = Rebinding(monkeypatch, 0)
    result = run(origin.url("allowed.test"))
    assert result
    assert origin.hits
    assert set(dns.connects) == {"127.0.0.1"}  # an address, never the name: no re-resolution
    assert set(origin.hosts) == {f"allowed.test:{origin.port}"}  # the Host header is kept
    if run is s3:
        assert result == [OBJECT]


def test_the_check_and_the_connection_use_one_lookup(
    origin: Origin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pinned backend resolves once per connection and connects to that answer."""

    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "127.0.0.0/8")
    dns = Rebinding(monkeypatch, 0)
    backend = guard._PinnedBackend()
    stream = backend.connect_tcp("pin.test", origin.port, timeout=5.0)
    stream.close()
    assert dns.lookups == {"pin.test": 1} and dns.connects == ["127.0.0.1"]
    dns.public_answers = 99  # from now on the name looks public; a stale answer is not reused
    with pytest.raises(httpx.ConnectError):
        guard.pinned_transport().handle_request(httpx.Request("GET", origin.url("other.test")))
    assert dns.connects[-1] == "93.184.216.34"  # the checked answer, as an address


# --- redirects --------------------------------------------------------------------------------


@pytest.mark.parametrize("run", RUNNERS, ids=IDS)
def test_a_redirect_to_a_private_address_is_refused(
    run: Callable[[str], Any], origin: Origin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "allowed.test")
    Rebinding(monkeypatch, 0)
    origin.redirect_all = True
    quietly(lambda: run(origin.url("allowed.test")))
    assert origin.hits, "the first request went out"
    assert not [h for h in origin.hits if h.startswith("/secret")], origin.hits


def test_redirect_chains_stop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://93.184.216.34/again"})

    with guard.guarded_client(transport=httpx.MockTransport(handler), follow_redirects=True) as c:
        with pytest.raises(httpx.TooManyRedirects):
            c.get("http://93.184.216.34/start")
        assert c.max_redirects == guard.MAX_REDIRECTS


# --- caps -------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["/stream", "/big"])
@pytest.mark.parametrize("run", RUNNERS, ids=IDS)
def test_bodies_stop_at_the_cap_without_being_buffered(
    run: Callable[[str], Any], mode: str, origin: Origin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "allowed.test")
    Rebinding(monkeypatch, 0)
    outcome: list[Any] = []
    error = quietly(lambda: outcome.append(run(origin.url("allowed.test", mode))))
    assert origin.hits
    # the 64 MiB stream is abandoned near the cap (plus what the socket buffers held)
    assert origin.sent < 32 * 1024 * 1024, origin.sent
    if outcome and run is not robots_txt:
        assert outcome[0] in ([], None), outcome  # nothing over the cap became an item
    else:
        assert error is not None or run is robots_txt


def test_the_s3_download_is_capped_by_bytes_read(
    origin: Origin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "allowed.test")
    Rebinding(monkeypatch, 0)
    with pytest.raises(TooLarge):
        s3(origin.url("allowed.test", "/stream"))  # the listing says 13 bytes; the body is not
    assert origin.sent < 32 * 1024 * 1024
    assert not list(Path(os.environ["SEMASCOPE_DATA_DIR"]).rglob(".part-*"))


def test_a_request_cap_and_the_client_cap_both_hold(
    origin: Origin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "127.0.0.1/32")
    with guard.guarded_client(max_bytes=CAP) as client:
        with pytest.raises(TooLarge):
            client.get(origin.url("127.0.0.1", "/stream/x"))  # read() without get_capped
        with pytest.raises(TooLarge):
            guard.get_capped(client, origin.url("127.0.0.1", "/stream/x"), 64 * 1024)
        resp, body = guard.get_capped(client, origin.url("127.0.0.1", "/page"), 64 * 1024)
        assert resp.status_code == 200 and b"hello page" in body
    with guard.service_client(max_bytes=CAP) as client:
        with pytest.raises(TooLarge):
            client.get(origin.url("127.0.0.1", "/stream/x"))
    assert origin.sent < 32 * 1024 * 1024


def _declares(size: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": str(size)}, content=b"{}")

    return httpx.MockTransport(handler)


def test_operator_services_have_a_cap_and_timeouts() -> None:
    from oss_pipeline.clients.mineru import MineruClient
    from oss_pipeline.clients.tika import TikaClient
    from oss_pipeline.embedders.openai_embedder import OpenAICompatibleEmbedder
    from oss_pipeline.embedders.sidecar_embedder import SidecarEmbedder
    from oss_pipeline.stages.ner import RemoteProvider

    over = guard.SERVICE_MAX_BYTES + 1
    clients: list[httpx.Client] = [
        SidecarEmbedder("m", "http://embed.lan", transport=_declares(over))._client,
        OpenAICompatibleEmbedder("m", "http://embed.lan", transport=_declares(over))._client,
        RemoteProvider("http://ner.lan", transport=_declares(over))._client,
        MineruClient("http://mineru.lan", transport=_declares(over))._client,
    ]
    tika = TikaClient("http://tika.lan")
    tika._http._transport = _declares(guard.MAX_BYTES_CEILING + 1)
    clients.append(tika._http)
    for client in clients:
        timeout = client.timeout
        assert None not in (timeout.connect, timeout.read, timeout.write, timeout.pool)
        assert not client.follow_redirects
        with pytest.raises(TooLarge):
            client.get("http://service.lan/x")


def test_every_timeout_is_set() -> None:
    for client in (guard.guarded_client(), guard.guarded_client(timeout=15.0)):
        t = client.timeout
        assert t.connect == guard.CONNECT_TIMEOUT_S and t.read is not None
        assert t.write is not None and t.pool is not None
        assert client._trust_env is False  # an environment proxy would resolve names itself
    for bad in (None, httpx.Timeout(None), httpx.Timeout(5.0, read=None)):
        with pytest.raises(ValueError):
            guard.guarded_client(timeout=bad)
        with pytest.raises(ValueError):
            guard.service_client(timeout=bad)


def test_a_slow_drip_stops_at_the_deadline(origin: Origin, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "127.0.0.1/32")
    with guard.guarded_client(timeout=5.0, deadline_s=0.3) as client:
        with pytest.raises(httpx.ReadTimeout):
            guard.get_capped(client, origin.url("127.0.0.1", "/drip/x"))


# --- TLS: the certificate is checked for the name, while the connection goes to the address --


def _tls_server(name: str, tmp: Path) -> tuple[ThreadingHTTPServer, ssl.SSLContext, list[str]]:
    pytest.importorskip("cryptography")
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    (tmp / "cert.pem").write_bytes(cert_pem)
    (tmp / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def do_GET(self) -> None:  # noqa: N802
            seen.append(f"host={self.headers.get('Host')}")
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(tmp / "cert.pem", tmp / "key.pem")
    server_ctx.sni_callback = lambda sock, sni, ctx: seen.append(f"sni={sni}")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = server_ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client_ctx = ssl.create_default_context(cafile=str(tmp / "cert.pem"))
    return server, client_ctx, seen


def test_tls_verifies_the_host_name_on_a_pinned_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "pinned.test,other.test")
    dns = Rebinding(monkeypatch, 0)
    server, client_ctx, seen = _tls_server("pinned.test", tmp_path)
    port = server.server_address[1]
    try:
        with guard.guarded_client(verify=client_ctx) as client:
            resp, body = guard.get_capped(client, f"https://pinned.test:{port}/x")
            assert resp.status_code == 200 and body == b"ok"
            assert dns.connects == ["127.0.0.1"]
            assert seen == ["sni=pinned.test", f"host=pinned.test:{port}"]
            # the same address under a name the certificate does not carry is refused
            with pytest.raises(httpx.ConnectError, match="certificate|hostname"):
                client.get(f"https://other.test:{port}/x")
    finally:
        server.shutdown()
        server.server_close()


def test_a_refused_host_raises_source_not_allowed_from_the_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Rebinding(monkeypatch, 0)
    transport = guard.pinned_transport()
    with pytest.raises(SourceNotAllowed):
        transport.handle_request(httpx.Request("GET", "http://private.test/"))
