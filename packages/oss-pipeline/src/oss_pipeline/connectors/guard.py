"""What a source may reach: folders under the source roots, and public network addresses.

Paths. Connectors that read local files (filesystem, mail, archive, table, rdf
files) may only read under `SEMASCOPE_SOURCE_ROOTS` (default `/import`, the read-only import
mount, and the demo corpus shipped in the image); upload sources only under their own collection's
upload area. Paths are resolved, symlinks included, before the comparison, so
a link that points out of a root is refused rather than followed.

Network. Every URL a source, a user or a document supplies (web pages, sitemaps,
robots.txt, feeds and their links, SPARQL endpoints, S3 endpoints, webhook
deliveries) goes through `guarded_client` (S3 through `guard_s3_client`), and
reaches only globally routable addresses (`ipaddress.is_global`, not
multicast): loopback, private, carrier-grade NAT, link-local, documentation,
reserved and unspecified addresses are refused, and so are IPv6 forms that
carry an IPv4 address (IPv4-mapped and NAT64 addresses are judged by the IPv4
address inside; 6to4 and Teredo are refused). The host is resolved once per
connection, checked, and the connection made to the checked address (TLS
still verifies the host name), so a name that resolves differently at
connection time cannot reach a private address; every request and every
redirect is also checked before it is sent. An operator who needs an
internal host (a MinIO on the LAN, say) lists it in
`SEMASCOPE_ALLOWED_PRIVATE_HOSTS` (host names or CIDR ranges). Every client
has its timeouts set and a cap on response bodies; a source's `max_bytes`
may set a connector's cap, but never above `MAX_BYTES_CEILING`.

Services the operator configures (Tika, MinerU, embedding and NER providers)
use `service_client`: timeouts, no redirects and a body cap, but no address
policy, since their URLs come only from the deployment's settings.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpcore
import httpx

PATH_CONNECTORS = ("filesystem", "mail", "archive", "table", "rdf", "upload")
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
MAX_BYTES_CEILING = 1024 * 1024 * 1024  # 1 GiB: no source setting goes above it
NAT64 = ipaddress.ip_network("64:ff9b::/96")


class SourceNotAllowed(ValueError):  # noqa: N818 - reads better at the raise sites
    """A source setting points somewhere a source may not read."""


class Unresolvable(SourceNotAllowed):  # noqa: N818
    """The host name did not resolve: possibly a passing DNS failure, not a refusal."""


# --- paths ------------------------------------------------------------------------------------


def _settings() -> Any:
    from oss_core.config import get_settings

    return get_settings()


def source_roots() -> list[Path]:
    """The folders file-reading sources may read, resolved."""

    raw = str(_settings().semascope_source_roots or "")
    parts = [p.strip() for chunk in raw.split(",") for p in chunk.split(":")]
    return [Path(p).expanduser().resolve() for p in parts if p]


def upload_roots(collection: str) -> list[Path]:
    from oss_pipeline.connectors.upload import upload_root

    return [upload_root(Path(_settings().semascope_data_dir), collection).resolve()]


def all_upload_roots() -> list[Path]:
    return [(Path(_settings().semascope_data_dir) / "uploads").resolve()]


def roots_for(connector: str, collection: str) -> list[Path]:
    return upload_roots(collection) if connector == "upload" else source_roots()


def within(path: Path, roots: Iterable[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def confine_path(value: str | Path, roots: list[Path]) -> Path:
    """The path, resolved through symlinks, if it lies under one of the roots."""

    if not str(value).strip():
        raise SourceNotAllowed("a path is required")
    resolved = Path(str(value)).expanduser().resolve()
    if not roots or not within(resolved, roots):
        allowed = ", ".join(str(r) for r in roots) or "none configured"
        raise SourceNotAllowed(f"{value} is outside the source roots ({allowed})")
    return resolved


def confined_files(files: Iterable[Path], roots: list[Path]) -> Iterator[Path]:
    """The files that still lie under a root once their symlinks are resolved."""

    for file in files:
        try:
            if within(file.resolve(), roots):
                yield file
        except OSError:
            continue


def check_source(connector: str, settings: dict[str, Any], collection: str) -> None:
    """Refuse a source whose settings reach outside what that connector may read.

    Network addresses are checked when the source runs (an address can change), so here
    only the URL schemes are.
    """

    if connector in PATH_CONNECTORS and not (connector == "rdf" and settings.get("endpoint")):
        confine_path(str(settings.get("path") or ""), roots_for(connector, collection))
    urls = [str(u) for u in settings.get("urls") or []] if connector in ("web", "feed") else []
    for key in ("endpoint", "endpoint_url"):
        if connector in ("rdf", "s3") and settings.get(key):
            urls.append(str(settings[key]))
    for url in urls:
        if urlparse(url).scheme not in ("http", "https"):
            raise SourceNotAllowed(f"{url} is not an http(s) URL")
    if "max_bytes" in settings:
        byte_cap(settings, DEFAULT_MAX_BYTES, strict=True)


def byte_cap(settings: dict[str, Any], default: int, *, strict: bool = False) -> int:
    """A source's `max_bytes` (or `default`), never above MAX_BYTES_CEILING.

    `strict` (when a source is created) refuses a value above the ceiling; at run time such
    a value is clamped to it. A value that is not a positive whole number is always refused.
    """

    raw = settings.get("max_bytes", default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise SourceNotAllowed(f"max_bytes {raw!r} is not a whole number") from exc
    if value <= 0 or isinstance(raw, bool):
        raise SourceNotAllowed(f"max_bytes {raw!r} must be a positive number of bytes")
    if value > MAX_BYTES_CEILING:
        if strict:
            raise SourceNotAllowed(f"max_bytes {value} is above the ceiling of {MAX_BYTES_CEILING}")
        return MAX_BYTES_CEILING
    return value


# --- network ----------------------------------------------------------------------------------


def _allowed_private() -> tuple[set[str], list[ipaddress.IPv4Network | ipaddress.IPv6Network]]:
    hosts: set[str] = set()
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in str(_settings().semascope_allowed_private_hosts or "").split(","):
        item = item.strip().lower()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            hosts.add(item)
    return hosts, nets


def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Globally routable unicast; an IPv6 form of an IPv4 address is judged by that address."""

    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return _is_public(address.ipv4_mapped)
        if address in NAT64:
            return _is_public(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
        if address.sixtofour is not None or address.teredo is not None:
            return False
    return address.is_global and not address.is_multicast


def _resolve(host: str, port: int | None) -> list[str]:
    """The addresses a host name resolves to, in the resolver's order, each once."""

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise Unresolvable(f"cannot resolve {host}") from exc
    out: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]  # drop an IPv6 zone id
        if address not in out:
            out.append(address)
    return out


def checked_addresses(
    host: str, port: int | None = None, *, label: str = "", resolve_allowed: bool = True
) -> list[str]:
    """Resolve `host` once and return its addresses if every one of them may be reached.

    Raises SourceNotAllowed if any address is private and not allowed by
    SEMASCOPE_ALLOWED_PRIVATE_HOSTS, and Unresolvable if the name does not resolve. The
    pinned transport connects to exactly these addresses, so the name is never resolved
    a second time between the check and the connection. A host allowed by name is not
    checked; with `resolve_allowed` false it is not even resolved (an empty list).
    """

    host = host.strip("[]").lower()
    if not host:
        raise SourceNotAllowed(f"{label or 'a URL'} has no host")
    allowed_hosts, allowed_nets = _allowed_private()
    if host in allowed_hosts:
        return _resolve(host, port) if resolve_allowed else []
    addresses = _resolve(host, port)
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if _is_public(address) or any(address in net for net in allowed_nets):
            continue
        raise SourceNotAllowed(f"{label or host} resolves to a private address; refused")
    return addresses


def refuse_private(url: str) -> None:
    """Raise SourceNotAllowed unless every address the URL's host resolves to is public."""

    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise SourceNotAllowed(f"{url} is not an http(s) URL")
    if not parts.hostname:
        raise SourceNotAllowed(f"{url} has no host")
    try:
        port = parts.port
    except ValueError as exc:
        raise SourceNotAllowed(f"{url} has an invalid port") from exc
    checked_addresses(parts.hostname, port, label=url, resolve_allowed=False)


class TooLarge(ValueError):  # noqa: N818
    """A response body went over the size cap."""


MAX_BYTES_EXTENSION = "semascope.max_bytes"  # per-request cap: client.get(..., extensions=...)
SERVICE_MAX_BYTES = 256 * 1024 * 1024  # default cap for an operator-configured service
CONNECT_TIMEOUT_S = 10.0
DEFAULT_TIMEOUT_S = 30.0
MAX_REDIRECTS = 10
BODY_DEADLINE_S = 900.0  # a guarded response body is read in at most this long, in total


class _PinnedBackend(httpcore.SyncBackend):
    """Connects to the addresses `checked_addresses` approved, never to a fresh resolution.

    httpcore hands the URL's host name to `connect_tcp` and, for https, starts TLS on the
    returned stream with that same host name as the SNI and certificate name; the Host
    header comes from the URL. So swapping the name for the checked address here pins the
    connection without changing what the server or the certificate check sees.
    """

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        error: Exception | None = None
        for address in checked_addresses(host, port):
            try:
                return super().connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                error = exc
        raise error or httpcore.ConnectError(f"no address to connect to for {host}")


def pinned_transport(**kwargs: Any) -> httpx.HTTPTransport:
    """An httpx transport whose every new connection goes through `_PinnedBackend`."""

    if kwargs.get("proxy") is not None or kwargs.get("uds") is not None:
        raise ValueError("a pinned transport connects directly; no proxy or socket path")
    transport = httpx.HTTPTransport(**kwargs)
    pool = getattr(transport, "_pool", None)
    if not isinstance(pool, httpcore.ConnectionPool) or not hasattr(pool, "_network_backend"):
        # fail closed if a future httpx/httpcore moves this: never fall back to an unpinned one
        raise RuntimeError("cannot pin addresses with this httpx/httpcore version")
    pool._network_backend = _PinnedBackend()
    return transport


class _CappedStream(httpx.SyncByteStream):
    def __init__(
        self, inner: Any, cap: int, url: str, deadline_s: float | None, request: httpx.Request
    ) -> None:
        self.inner = inner
        self.cap = cap
        self.url = url
        self.deadline_s = deadline_s
        self.request = request

    def __iter__(self) -> Iterator[bytes]:
        total = 0
        started = time.monotonic()
        for chunk in self.inner:
            total += len(chunk)
            if total > self.cap:
                raise TooLarge(f"{self.url} is over the cap of {self.cap} bytes")
            if self.deadline_s is not None and time.monotonic() - started > self.deadline_s:
                raise httpx.ReadTimeout(
                    f"{self.url} took over {self.deadline_s:.0f}s to read", request=self.request
                )
            yield chunk

    def close(self) -> None:
        self.inner.close()


def _cap_hook(default: int, deadline_s: float | None) -> Any:
    """A response hook that caps the body before anything reads it (redirect bodies too)."""

    def hook(response: httpx.Response) -> None:
        request = response.request
        cap = min(int(request.extensions.get(MAX_BYTES_EXTENSION) or default), MAX_BYTES_CEILING)
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > cap:
            raise TooLarge(f"{request.url} is {declared} bytes; the cap is {cap}")
        response.stream = _CappedStream(response.stream, cap, str(request.url), deadline_s, request)

    return hook


def _timeout(value: Any) -> httpx.Timeout:
    """A timeout with every part set: a number is the read/write/pool limit, connect shorter."""

    if isinstance(value, httpx.Timeout):
        parts = (value.connect, value.read, value.write, value.pool)
        if any(p is None for p in parts):
            raise ValueError("an outbound client needs every timeout set")
        return value
    if value is None or isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("an outbound client needs a timeout in seconds")
    return httpx.Timeout(float(value), connect=min(float(value), CONNECT_TIMEOUT_S))


_TRANSPORT_KEYS = ("verify", "cert", "http1", "http2", "limits", "retries", "local_address")


def _hooks(kwargs: dict[str, Any], request: list[Any], response: list[Any]) -> dict[str, Any]:
    hooks = dict(kwargs.pop("event_hooks", None) or {})
    hooks["request"] = [*request, *hooks.get("request", [])]
    hooks["response"] = [*response, *hooks.get("response", [])]
    return hooks


def guarded_client(
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    deadline_s: float | None = BODY_DEADLINE_S,
    transport: httpx.BaseTransport | None = None,
    **kwargs: Any,
) -> httpx.Client:
    """The client for any URL a source, a user or a document supplies.

    Every new connection resolves the host once, refuses it unless every address is public
    (or allowed by SEMASCOPE_ALLOWED_PRIVATE_HOSTS), and connects to the checked address, so
    a name that resolves differently at connection time (DNS rebinding) cannot reach a
    private address. TLS still verifies the certificate for the host name. Each request and
    redirect is also checked up front with `refuse_private`; redirects stop at
    MAX_REDIRECTS; every timeout is set; bodies stop at `max_bytes` (or the request's
    MAX_BYTES_EXTENSION) and at `deadline_s`. Environment proxies are ignored: a proxy
    would resolve the name itself, out of reach of the check. `transport` is for tests.
    """

    transport_kwargs = {k: kwargs.pop(k) for k in _TRANSPORT_KEYS if k in kwargs}
    kwargs["timeout"] = _timeout(kwargs.get("timeout", DEFAULT_TIMEOUT_S))
    kwargs.setdefault("max_redirects", MAX_REDIRECTS)
    kwargs["trust_env"] = False
    cap = min(int(max_bytes), MAX_BYTES_CEILING)
    hooks = _hooks(kwargs, [_check_request], [_cap_hook(cap, deadline_s)])
    return httpx.Client(
        transport=transport or pinned_transport(**transport_kwargs),
        event_hooks=hooks,
        **kwargs,
    )


def service_client(
    *,
    max_bytes: int = SERVICE_MAX_BYTES,
    transport: httpx.BaseTransport | None = None,
    **kwargs: Any,
) -> httpx.Client:
    """The client for a service the operator configured (Tika, MinerU, an embedding, NER or
    extraction provider): no address policy, because these usually sit on a private network
    and their URLs come only from the deployment's settings, never from a source or a user.
    Without a policy there is nothing for DNS rebinding to get around, so no pinning either.
    Every timeout is set, redirects are not followed, and bodies stop at `max_bytes`.
    """

    kwargs["timeout"] = _timeout(kwargs.get("timeout", DEFAULT_TIMEOUT_S))
    kwargs["follow_redirects"] = False
    cap = min(int(max_bytes), MAX_BYTES_CEILING)
    hooks = _hooks(kwargs, [], [_cap_hook(cap, None)])
    return httpx.Client(transport=transport, event_hooks=hooks, **kwargs)


def _check_request(request: httpx.Request) -> None:
    refuse_private(str(request.url))


def get_capped(
    client: httpx.Client, url: str, max_bytes: int = DEFAULT_MAX_BYTES, **kwargs: Any
) -> tuple[httpx.Response, bytes]:
    """GET a URL, reading at most `max_bytes` of body (TooLarge beyond that)."""

    extensions = {**(kwargs.pop("extensions", None) or {}), MAX_BYTES_EXTENSION: max_bytes}
    with client.stream("GET", url, extensions=extensions, **kwargs) as resp:
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > max_bytes:  # decoded size: a compressed body may expand past the cap
                raise TooLarge(f"{url} is over the cap of {max_bytes} bytes")
            chunks.append(chunk)
    return resp, b"".join(chunks)


# --- S3 (boto3) -------------------------------------------------------------------------------


S3_RESPONSE_MAX_BYTES = 8 * 1024 * 1024  # a listing page of 1000 keys is well under 2 MiB


class _RawBody:
    """The part of urllib3's response botocore reads (`stream`, `read`, `close`), over httpx."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self._chunks = response.iter_raw()
        self._buffer = bytearray()

    def stream(self, amt: int = 65536, decode_content: bool | None = None) -> Iterator[bytes]:
        while True:
            data = self.read(amt)
            if not data:
                return
            yield data

    def read(self, amt: int | None = None, *_: Any, **__: Any) -> bytes:
        while amt is None or len(self._buffer) < amt:
            chunk = next(self._chunks, None)
            if chunk is None:
                break
            self._buffer += chunk
        size = len(self._buffer) if amt is None else amt
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def close(self) -> None:
        self._response.close()


def guard_s3_client(client: Any, *, max_bytes: int = MAX_BYTES_CEILING) -> Any:
    """Send every request of a boto3 client through a guarded, pinned httpx client.

    botocore's `before-send` event lets a handler answer a request itself; this one sends
    the signed request with `guarded_client` (method, URL and headers unchanged, so the
    signature still matches) and hands the streamed answer back to botocore. The endpoint
    host is checked and pinned on every connection, as for any other URL. An object body
    (GetObject) stops at `max_bytes`; every other answer (listings, errors) at
    S3_RESPONSE_MAX_BYTES, since botocore reads those whole.
    """

    from botocore import exceptions as boto_errors
    from botocore.awsrequest import AWSResponse

    http = guarded_client(
        timeout=httpx.Timeout(120.0, connect=CONNECT_TIMEOUT_S),
        follow_redirects=False,
        max_bytes=max_bytes,
        deadline_s=None,  # an object download may take long; the read timeout still holds
    )

    def send(request: Any, event_name: str = "", **_: Any) -> Any:
        cap = max_bytes if event_name.endswith(".GetObject") else S3_RESPONSE_MAX_BYTES
        body = request.body
        if hasattr(body, "read"):
            body = body.read()
        outgoing = http.build_request(
            request.method,
            request.url,
            headers=list(request.headers.items()),
            content=body if body else None,
            extensions={MAX_BYTES_EXTENSION: cap},
        )
        try:
            response = http.send(outgoing, stream=True)
        except httpx.ConnectTimeout as exc:
            raise boto_errors.ConnectTimeoutError(endpoint_url=request.url, error=exc) from exc
        except httpx.TimeoutException as exc:
            raise boto_errors.ReadTimeoutError(endpoint_url=request.url, error=exc) from exc
        except httpx.ConnectError as exc:
            raise boto_errors.EndpointConnectionError(endpoint_url=request.url, error=exc) from exc
        except httpx.HTTPError as exc:
            raise boto_errors.HTTPClientError(error=exc) from exc
        return AWSResponse(request.url, response.status_code, response.headers, _RawBody(response))

    client.meta.events.register("before-send", send)
    return client
