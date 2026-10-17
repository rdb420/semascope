"""Web connector: one page, a list of pages, a sitemap, or a bounded same-host crawl.

Settings: `urls` (list, required), `mode` (`pages` | `sitemap` | `crawl`),
`max_pages` (200), `max_depth` (2, crawl only), `include` / `exclude` (URL
regexes), `respect_robots` (true), `delay_seconds` (0.5), `user_agent`,
`max_bytes` (50 MiB per response), `scrape` (CSS rules for content, title and
fields: `oss_pipeline.scraper`). Fetched HTML goes to Tika like any other
document; the connector only discovers and downloads. Private address ranges
are refused on every request and redirect (see `guard`).

A page that cannot be read on this run (a 5xx, 429, 401 or 403 answer, a
timeout, a host name that does not resolve) is reported, not passed over, so
the run deletes nothing; a page that answers 404 or 410, is no longer linked or
is now excluded is gone. A crawl that stopped at `max_pages` deletes nothing
either. Sitemaps are each read once, nest at most three deep, and give at most
ten times `max_pages` URLs.
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.robotparser
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import urldefrag, urljoin, urlparse
from xml.etree import ElementTree as ET  # noqa: S405 - parsed with defusedxml below

import httpx
from defusedxml import ElementTree as SafeET

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.base import RunReport, path_fields, work_dir
from oss_pipeline.connectors.guard import (
    DEFAULT_MAX_BYTES,
    SourceNotAllowed,
    TooLarge,
    Unresolvable,
    byte_cap,
    get_capped,
    guarded_client,
    refuse_private,
)
from oss_pipeline.scraper import scrape, with_scraped

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


# Answers that say nothing about whether a page still exists: try again on the next run
UNSURE_STATUS = frozenset({401, 403, 408, 425, 429})


class WebConnector:
    name = "web"
    deletes_missing = True  # a page that answers 404 or 410, or is no longer linked, is gone

    def __init__(self) -> None:
        self.report = RunReport()

    # --- discovery -----------------------------------------------------------------------------

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        """Pages in the source's scope. A page that cannot be read now (a 5xx, 429 or 403
        answer, a timeout, a host that does not resolve) goes in the run report, so the run
        deletes nothing; when nothing at all could be read, the run fails."""

        self.report = report = RunReport()
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
        for seed in seeds:
            refuse_private(seed)  # a seed on a private address is a refused source
        tmp = work_dir(source, "web")
        yielded = 0

        with guarded_client(
            timeout=30.0, follow_redirects=True, headers={"User-Agent": agent}
        ) as client:
            robots = _Robots(agent, client) if bool(settings.get("respect_robots", True)) else None
            if mode == "sitemap":
                sitemap = _Sitemap(client, max_bytes, report, limit=max(max_pages * 10, 1000))
                for seed in seeds:
                    sitemap.read(seed)
                queue: deque[tuple[str, int]] = deque((u, 0) for u in sitemap.urls)
            else:
                queue = deque((u, 0) for u in seeds)
            seen: set[str] = set()
            hosts = {urlparse(u).netloc for u in seeds}
            while queue:
                if len(seen) >= max_pages:
                    report.truncated = True  # pages past the limit were not looked at
                    break
                url, depth = queue.popleft()
                url = urldefrag(url)[0]
                if url in seen or not _allowed(url, include, exclude):
                    continue
                try:
                    refuse_private(url)
                except Unresolvable as exc:
                    report.skip(url, exc)
                    continue
                except SourceNotAllowed:
                    continue  # a link to a private address is never fetched
                if robots and not robots.allowed(url):
                    continue
                seen.add(url)
                try:
                    resp, content = get_capped(client, url, max_bytes)
                except (TooLarge, SourceNotAllowed) as exc:
                    if isinstance(exc, Unresolvable):
                        report.skip(url, exc)
                    continue  # over the cap, or redirected to a private address: not indexed
                except httpx.HTTPError as exc:
                    report.skip(url, f"{type(exc).__name__}: {exc}")
                    continue
                if resp.status_code >= 500 or resp.status_code in UNSURE_STATUS:
                    report.skip(url, f"HTTP {resp.status_code}")
                    continue
                if resp.status_code != 200:
                    continue  # 404, 410 and the like: the page is gone
                media_type = (
                    resp.headers.get("content-type", "application/octet-stream")
                    .split(";")[0]
                    .strip()
                )
                page, scraped = scrape(content, media_type, source.settings)  # scraper.py
                digest = hashlib.sha256(page).hexdigest()
                path = tmp / (digest + _suffix(media_type))
                if not path.exists():
                    path.write_bytes(page)
                parts = urlparse(url)
                folders = [parts.netloc, *[p for p in parts.path.split("/")[:-1] if p]]
                yielded += 1
                yield SourceItem(
                    uri=url,
                    content_hash=digest,
                    modified_at=_last_modified(resp.headers.get("last-modified")),
                    size=len(page),
                    metadata=with_scraped(
                        scraped,
                        {
                            "path": str(path),
                            "media_type": media_type,
                            "depth": depth,
                            "fields": path_fields(folders, parts.path.rsplit("/", 1)[-1]),
                        },
                    ),
                )
                if mode == "crawl" and depth < max_depth and media_type == "text/html":
                    for link in _links(
                        url, content.decode(resp.encoding or "utf-8", errors="ignore")
                    ):
                        if urlparse(link).netloc in hosts and link not in seen:
                            queue.append((link, depth + 1))
                time.sleep(delay)
        if not yielded and report.unchecked:
            raise ConnectionError(f"no page could be read: {report.unchecked[0]}")

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


class _Sitemap:
    """The page URLs of sitemaps and sitemap indexes, at most `limit`, each sitemap read once.

    The first sitemap of a source must be readable (the run fails otherwise); a nested one
    that cannot be read goes in the run report. Indexes nest at most `MAX_DEPTH` deep.
    """

    MAX_DEPTH = 3

    def __init__(
        self, client: httpx.Client, max_bytes: int, report: RunReport, *, limit: int
    ) -> None:
        self.client = client
        self.max_bytes = max_bytes
        self.report = report
        self.limit = limit
        self.urls: list[str] = []
        self._read: set[str] = set()

    def read(self, url: str, depth: int = 0) -> None:
        url = urldefrag(url)[0]
        if url in self._read or depth > self.MAX_DEPTH:
            return
        self._read.add(url)
        try:
            refuse_private(url)
            resp, body = get_capped(self.client, url, self.max_bytes)
            resp.raise_for_status()
            root = SafeET.fromstring(body)
        except (httpx.HTTPError, ET.ParseError, TooLarge, SourceNotAllowed) as exc:
            if depth == 0:
                raise ConnectionError(f"sitemap {url} could not be read: {exc}") from exc
            self.report.skip(url, f"{type(exc).__name__}: {exc}")
            return
        for el in root.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag != "loc" or not el.text:
                continue
            loc = el.text.strip()
            if loc.endswith(".xml"):
                self.read(loc, depth + 1)
            elif len(self.urls) >= self.limit:
                self.report.truncated = True
                return
            else:
                self.urls.append(loc)


def _sitemap_urls(
    client: httpx.Client, sitemap_url: str, max_bytes: int = DEFAULT_MAX_BYTES
) -> list[str]:
    """The page URLs of a sitemap (kept for callers outside a source run)."""

    sitemap = _Sitemap(client, max_bytes, RunReport(), limit=100_000)
    sitemap.read(sitemap_url)
    return sitemap.urls


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
