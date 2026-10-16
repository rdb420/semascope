"""Feed connector: RSS and Atom entries as documents.

Settings: `urls` (list), `max_entries` (200 per feed), `fetch_links`
(false: index the entry's own content; true: download the linked page),
`max_bytes` (50 MiB per response). Private addresses are refused on every
request and redirect (see `guard`).
Entries are deduplicated by id or link; the entry's published date becomes
the document's modified time.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from time import mktime

import feedparser
import httpx

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
from oss_pipeline.connectors.web import USER_AGENT


class FeedConnector:
    name = "feed"

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        urls = [str(u) for u in source.settings.get("urls") or []]
        if not urls:
            raise ValueError("feed connector needs settings.urls")
        max_entries = int(source.settings.get("max_entries", 200))
        fetch_links = bool(source.settings.get("fetch_links", False))
        max_bytes = byte_cap(source.settings, DEFAULT_MAX_BYTES)
        tmp = Path(tempfile.gettempdir()) / "semascope-feed"
        tmp.mkdir(parents=True, exist_ok=True)
        with guarded_client(
            timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as client:
            for url in urls:
                refuse_private(url)
                try:
                    resp, feed_body = get_capped(client, url, max_bytes)
                    resp.raise_for_status()
                except (httpx.HTTPError, TooLarge, SourceNotAllowed):
                    continue
                parsed = feedparser.parse(feed_body)
                for entry in parsed.entries[:max_entries]:
                    link = str(entry.get("link") or "")
                    entry_id = str(entry.get("id") or link)
                    if not entry_id:
                        continue
                    published = entry.get("published_parsed") or entry.get("updated_parsed")
                    modified = (
                        datetime.fromtimestamp(mktime(published), tz=UTC) if published else None
                    )
                    if fetch_links and link:
                        try:
                            page, content = get_capped(client, link, max_bytes)
                            page.raise_for_status()
                            media_type = (
                                page.headers.get("content-type", "text/html").split(";")[0].strip()
                            )
                        except (httpx.HTTPError, ValueError):  # TooLarge, SourceNotAllowed
                            continue
                    else:
                        body = ""
                        if entry.get("content"):
                            body = str(entry["content"][0].get("value") or "")
                        body = body or str(entry.get("summary") or "")
                        title = str(entry.get("title") or "")
                        head = f"<html><head><title>{title}</title></head>"
                        content = f"{head}<body><h1>{title}</h1>{body}</body></html>".encode()
                        media_type = "text/html"
                    digest = hashlib.sha256(content).hexdigest()
                    path = tmp / f"{hashlib.sha256(entry_id.encode()).hexdigest()}.html"
                    path.write_bytes(content)
                    yield SourceItem(
                        uri=link or entry_id,
                        content_hash=digest,
                        modified_at=modified,
                        size=len(content),
                        metadata={
                            "path": str(path),
                            "media_type": media_type,
                            "feed": url,
                            "title": str(entry.get("title") or ""),
                        },
                    )

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(
            item=item, path=str(item.metadata["path"]), media_type=item.metadata.get("media_type")
        )

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []
