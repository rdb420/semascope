"""Web connector: one page, a list of pages, a sitemap, or a bounded same-host crawl.

Settings: `urls` (list, required), `mode` (`pages` | `sitemap` | `crawl`),
`max_pages` (200), `max_depth` (2, crawl only), `include` / `exclude` (URL
regexes), `respect_robots` (true), `delay_seconds` (0.5), `user_agent`,
`max_bytes` (50 MiB per response). Fetched HTML goes to Tika like any other
document; the connector only discovers and downloads. Private address ranges
are refused on every request and redirect (see `guard`).
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import time
import urllib.robotparser
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from xml.etree import ElementTree as ET  # noqa: S405 - parsed with defusedxml below

import httpx
from defusedxml import ElementTree as SafeET

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.guard import (
    DEFAULT_MAX_BYTES,
    SourceNotAllowed,
    TooLarge,
    byte_cap,
    get_capped,
    guarded_client,
    refuse_private,
)

USER_AGENT = "semascope/0.1 (+https://github.com/rdb420/semascope)"
_HREF = re.compile(r"""<a\s[^>]*href\s*=\s*["']([^"']+)["']""", re.I)
_SKIP_EXT = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".css",
    ".js",
    ".ico",
    ".woff",
    ".woff2",
    ".mp4",
    ".mp3",
    ".zip",
)


class WebConnector:
    name = "web"

    def __init__(self) -> None:
        self._cache: dict[str, Path] = {}

    # --- discovery -----------------------------------------------------------------------------

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        settings = source.settings
        seeds = [str(u) for u in settings.get("urls") or []]
        if not seeds:
            raise ValueError("web connector needs settings.urls")
        mode = str(settings.get("mode", "pages"))
        max_pages = int(settings.get("max_pages", 200))
        max_depth = int(settings.get("max_depth", 2))
        include = [re.compile(p) for p in settings.get("include") or []]
        exclude = [re.compile(p) for p in settings.get("exclude") or []]
        delay = float(settings.get("delay_seconds", 0.5))
        agent = str(settings.get("user_agent") or USER_AGENT)
        max_bytes = byte_cap(settings, DEFAULT_MAX_BYTES)

        with guarded_client(
            timeout=30.0, follow_redirects=True, headers={"User-Agent": agent}
        ) as client:
            robots = _Robots(agent, client) if bool(settings.get("respect_robots", True)) else None
            if mode == "sitemap":
                queue: deque[tuple[str, int]] = deque(
                    (u, 0) for seed in seeds for u in _sitemap_urls(client, seed, max_bytes)
                )
            else:
                queue = deque((u, 0) for u in seeds)
            seen: set[str] = set()
            hosts = {urlparse(u).netloc for u in seeds}
            while queue and len(seen) < max_pages:
                url, depth = queue.popleft()
                url = urldefrag(url)[0]
                if url in seen or not _allowed(url, include, exclude):
                    continue
                refuse_private(url)
                if robots and not robots.allowed(url):
                    continue
                seen.add(url)
                try:
                    resp, content = get_capped(client, url, max_bytes)
                except (httpx.HTTPError, TooLarge, SourceNotAllowed):
                    continue
                if resp.status_code != 200:
                    continue
                media_type = (
                    resp.headers.get("content-type", "application/octet-stream")
                    .split(";")[0]
                    .strip()
                )
                path = (
                    Path(tempfile.gettempdir())
                    / "semascope-web"
                    / (hashlib.sha256(url.encode()).hexdigest() + _suffix(media_type))
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                self._cache[url] = path
                yield SourceItem(
                    uri=url,
                    content_hash=hashlib.sha256(content).hexdigest(),
                    modified_at=_last_modified(resp.headers.get("last-modified")),
                    size=len(content),
                    metadata={"path": str(path), "media_type": media_type, "depth": depth},
                )
                if mode == "crawl" and depth < max_depth and media_type == "text/html":
                    for link in _links(
                        url, content.decode(resp.encoding or "utf-8", errors="ignore")
                    ):
                        if urlparse(link).netloc in hosts and link not in seen:
                            queue.append((link, depth + 1))
                time.sleep(delay)

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(
            item=item, path=str(item.metadata["path"]), media_type=item.metadata.get("media_type")
        )

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []


# --- helpers --------------------------------------------------------------------------------


class _Robots:
    """robots.txt per host, fetched through the guarded client."""

    def __init__(self, agent: str, client: httpx.Client) -> None:
        self.agent = agent
        self.client = client
        self._parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    def allowed(self, url: str) -> bool:
        parts = urlparse(url)
        root = f"{parts.scheme}://{parts.netloc}"
        parser = self._parsers.get(root)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser(f"{root}/robots.txt")
            lines: list[str] = []
            try:
                resp, body = get_capped(self.client, f"{root}/robots.txt", 512 * 1024)
                if resp.status_code == 200:
                    lines = body.decode("utf-8", errors="ignore").splitlines()
            except Exception:  # noqa: BLE001 - unreadable robots means no restriction
                lines = []
            parser.parse(lines)
            self._parsers[root] = parser
        return parser.can_fetch(self.agent, url)


def _allowed(url: str, include: list[re.Pattern[str]], exclude: list[re.Pattern[str]]) -> bool:
    if urlparse(url).scheme not in ("http", "https"):
        return False
    if url.lower().endswith(_SKIP_EXT):
        return False
    if include and not any(p.search(url) for p in include):
        return False
    return not any(p.search(url) for p in exclude)


_refuse_private = refuse_private  # the name feed and older callers import


def _links(base: str, html: str) -> list[str]:
    out: list[str] = []
    for href in _HREF.findall(html):
        link = urldefrag(urljoin(base, href.strip()))[0]
        if link and link not in out:
            out.append(link)
    return out


def _sitemap_urls(
    client: httpx.Client, sitemap_url: str, max_bytes: int = DEFAULT_MAX_BYTES, depth: int = 0
) -> list[str]:
    if depth > 3:
        return []
    try:
        resp, body = get_capped(client, sitemap_url, max_bytes)
        resp.raise_for_status()
        root = SafeET.fromstring(body)
    except (httpx.HTTPError, ET.ParseError, TooLarge, SourceNotAllowed):
        return []
    urls: list[str] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "loc" and el.text:
            loc = el.text.strip()
            if loc.endswith(".xml"):
                urls.extend(_sitemap_urls(client, loc, max_bytes, depth + 1))
            else:
                urls.append(loc)
    return urls


def _last_modified(value: str | None) -> datetime | None:
    if not value:
        return None
    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _suffix(media_type: str) -> str:
    return {
        "text/html": ".html",
        "application/pdf": ".pdf",
        "application/xhtml+xml": ".html",
        "text/plain": ".txt",
    }.get(media_type, "")
