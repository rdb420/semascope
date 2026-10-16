"""What a source may reach: folders under the source roots, and public network addresses.

Paths. Connectors that read local files (filesystem, mail, archive, table, rdf
files) may only read under `SEMASCOPE_SOURCE_ROOTS` (default `/import`, the read-only import
mount, and the demo corpus shipped in the image); upload sources only under their own collection's
upload area. Paths are resolved, symlinks included, before the comparison, so
a link that points out of a root is refused rather than followed.

Network. Connectors that fetch URLs (web, feed, a SPARQL endpoint, an S3
endpoint) refuse loopback, private, link-local, multicast and reserved
addresses, checked on every request and every redirect. An operator who needs
an internal host (a MinIO on the LAN, say) lists it in
`SEMASCOPE_ALLOWED_PRIVATE_HOSTS` (host names or CIDR ranges). Response bodies
are read with a size cap.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

PATH_CONNECTORS = ("filesystem", "mail", "archive", "table", "rdf", "upload")
DEFAULT_MAX_BYTES = 50 * 1024 * 1024


class SourceNotAllowed(ValueError):  # noqa: N818 - reads better at the raise sites
    """A source setting points somewhere a source may not read."""


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
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def refuse_private(url: str) -> None:
    """Raise SourceNotAllowed unless every address the URL's host resolves to is public."""

    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise SourceNotAllowed(f"{url} is not an http(s) URL")
    host = (parts.hostname or "").lower()
    if not host:
        raise SourceNotAllowed(f"{url} has no host")
    allowed_hosts, allowed_nets = _allowed_private()
    if host in allowed_hosts:
        return
    try:
        infos = socket.getaddrinfo(host, parts.port or None)
    except socket.gaierror as exc:
        raise SourceNotAllowed(f"cannot resolve {host}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if _is_public(address) or any(address in net for net in allowed_nets):
            continue
        raise SourceNotAllowed(f"{url} resolves to a private address; refused")


def _check_request(request: httpx.Request) -> None:
    refuse_private(str(request.url))


def guarded_client(**kwargs: Any) -> httpx.Client:
    """An httpx client that checks every request, redirects included, with refuse_private."""

    hooks = dict(kwargs.pop("event_hooks", None) or {})
    hooks["request"] = [_check_request, *hooks.get("request", [])]
    return httpx.Client(event_hooks=hooks, **kwargs)


class TooLarge(ValueError):  # noqa: N818
    """A response body went over the size cap."""


def get_capped(
    client: httpx.Client, url: str, max_bytes: int = DEFAULT_MAX_BYTES, **kwargs: Any
) -> tuple[httpx.Response, bytes]:
    """GET a URL, reading at most `max_bytes` of body (TooLarge beyond that)."""

    with client.stream("GET", url, **kwargs) as resp:
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise TooLarge(f"{url} is {declared} bytes; the cap is {max_bytes}")
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise TooLarge(f"{url} is over the cap of {max_bytes} bytes")
            chunks.append(chunk)
    return resp, b"".join(chunks)
