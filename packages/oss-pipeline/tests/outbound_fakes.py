"""Test doubles for the outbound guard: a local origin server, a resolver that changes its
answer, and a connect recorder. Shared by the pipeline and server tests (loaded by path).

The origin listens on 127.0.0.1. Its paths take an optional mode prefix:

- `/r/...` answers 302 to `http://localhost:<port>/secret` (a private address), and so
  does every path while `redirect_all` is set;
- `/big/...` declares a 10 MiB body;
- `/stream/...` sends up to 64 MiB with no length, and counts what it managed to send;
- `/drip/...` sends a byte every 50 ms for up to 10 s.

An S3 listing (`/bkt`) is served normally under any prefix but `/r/`, so a capped object
download can be tested after a normal listing.

Without a prefix it serves what each client type asks for: a page, robots.txt, a sitemap,
an RSS feed, SPARQL JSON results, a webhook receiver and an S3 bucket (`bkt`, one object).
"""

from __future__ import annotations

import gzip
import ipaddress
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import httpcore
import pytest

PUBLIC = "93.184.216.34"  # a globally routable address the tests never actually reach
STREAM_TOTAL = 64 * 1024 * 1024
OBJECT = b"hello from s3"


class Origin:
    def __init__(self) -> None:
        self.hits: list[str] = []
        self.hosts: list[str] = []
        self.sent = 0  # bytes of /stream/ bodies written before the client stopped reading
        self.redirect_all = False  # answer every request as `/r/` does
        origin = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass

            def _reply(self, status: int, body: bytes, kind: str = "text/plain") -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.do_GET()

            def do_GET(self) -> None:  # noqa: N802
                origin.hits.append(self.path)
                path = urlparse(self.path).path
                host = self.headers.get("Host", "")
                origin.hosts.append(host)
                for mode in ("/big", "/stream", "/drip"):
                    if path in (f"{mode}/bkt", f"{mode}/bkt/"):
                        path = "/bkt"
                if origin.redirect_all or path.startswith("/r/"):
                    self.send_response(302)
                    self.send_header("Location", f"http://localhost:{origin.port}/secret")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if path.startswith("/big/"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(10 * 1024 * 1024))
                    self.end_headers()
                    return
                if path.startswith("/drip/"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    try:
                        for _ in range(200):
                            self.wfile.write(b"x")
                            self.wfile.flush()
                            time.sleep(0.05)
                    except OSError:
                        pass
                    return
                if path.startswith("/stream/"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    chunk = b"x" * 65536
                    try:
                        while origin.sent < STREAM_TOTAL:
                            self.wfile.write(chunk)
                            origin.sent += len(chunk)
                    except OSError:
                        pass
                    return
                if path == "/robots.txt":
                    self._reply(200, b"User-agent: *\nAllow: /\n")
                elif path == "/sitemap.xml":
                    body = (
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                        f"<url><loc>http://{host}/page</loc></url></urlset>"
                    )
                    self._reply(200, body.encode(), "application/xml")
                elif path == "/feed.xml":
                    body = (
                        '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
                        f"<item><title>i</title><link>http://{host}/page</link>"
                        "<guid>i1</guid><description>d</description></item></channel></rss>"
                    )
                    self._reply(200, body.encode(), "application/rss+xml")
                elif path == "/sparql":
                    results = (
                        b'{"head":{"vars":["s","p","o"]},"results":{"bindings":[{'
                        b'"s":{"type":"uri","value":"https://example.test/a"},'
                        b'"p":{"type":"uri","value":"http://www.w3.org/2000/01/rdf-schema#label"},'
                        b'"o":{"type":"literal","value":"A"}}]}}'
                    )
                    self._reply(200, results, "application/sparql-results+json")
                elif path == "/bkt" or path == "/bkt/":
                    body = (
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                        "<Name>bkt</Name><Prefix></Prefix><KeyCount>1</KeyCount>"
                        "<MaxKeys>1000</MaxKeys><IsTruncated>false</IsTruncated>"
                        "<Contents><Key>k.txt</Key><LastModified>2026-01-01T00:00:00.000Z"
                        f'</LastModified><ETag>"e"</ETag><Size>{len(OBJECT)}</Size>'
                        "<StorageClass>STANDARD</StorageClass></Contents></ListBucketResult>"
                    )
                    data = body.encode()
                    if "gzip" in self.headers.get("Accept-Encoding", ""):
                        data = gzip.compress(data)  # as MinIO does for larger listings
                        self.send_response(200)
                        self.send_header("Content-Encoding", "gzip")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                    else:
                        self._reply(200, data, "application/xml")
                elif path == "/bkt/k.txt":
                    self._reply(200, OBJECT, "application/octet-stream")
                elif path == "/hook":
                    self._reply(204, b"")
                else:
                    self._reply(200, b"<html><body><p>hello page</p></body></html>", "text/html")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, host: str, mode: str = "") -> str:
        return f"http://{host}:{self.port}{mode}"

    def reset(self) -> None:
        self.hits.clear()
        self.hosts.clear()
        self.sent = 0
        self.redirect_all = False

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@contextmanager
def origin_server() -> Iterator[Origin]:
    origin = Origin()
    try:
        yield origin
    finally:
        origin.close()


class Rebinding:
    """`socket.getaddrinfo` for `*.test` names: PUBLIC for the first `public_answers` lookups
    of a name, then 127.0.0.1; every other name resolves as usual. `connects` records each
    address a TCP connection was opened to; a connection to PUBLIC fails at once."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, public_answers: int) -> None:
        self.public_answers = public_answers
        self.lookups: dict[str, int] = {}
        self.connects: list[str] = []
        real_getaddrinfo = socket.getaddrinfo
        real_connect = httpcore.SyncBackend.connect_tcp

        def getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
            name = host.decode() if isinstance(host, bytes) else str(host)
            if not name.endswith(".test"):
                return real_getaddrinfo(host, port, *args, **kwargs)
            seen = self.lookups.get(name, 0)
            self.lookups[name] = seen + 1
            address = PUBLIC if seen < self.public_answers else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))]

        def connect_tcp(backend: Any, host: str, port: int, *args: Any, **kwargs: Any) -> Any:
            self.connects.append(host)
            if host == PUBLIC:
                raise httpcore.ConnectError("test: the public address is not reachable")
            return real_connect(backend, host, port, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
        monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect_tcp)

    def reached_private(self) -> bool:
        return any(not ipaddress.ip_address(a).is_global for a in self.connects if _ip(a))


def _ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def quietly(run: Callable[[], Any]) -> BaseException | None:
    """Run a client; return what it raised (the tests judge by what the origin saw)."""

    try:
        run()
    except Exception as exc:  # noqa: BLE001 - any refusal or failure is fine here
        return exc
    return None
