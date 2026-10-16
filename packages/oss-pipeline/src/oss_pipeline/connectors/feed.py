"""Feed connector: RSS and Atom entries as documents.

Settings: `urls` (list), `max_entries` (200 per feed), `fetch_links`
(false: index the entry's own content; true: download the linked page),
`max_bytes` (50 MiB per response). Private addresses are refused on every
request and redirect (see `guard`).
Entries are deduplicated by id or link; the entry's published date becomes
the document's modified time.

A feed shows only its latest entries, so an entry that rolls off is not
deleted: feed sources never delete by absence (unless the source sets
`delete_missing: true`). A feed that cannot be read fails the run (after the
source's other feeds were read); a linked page that cannot be read now is
reported and tried again on the next run.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime
from time import mktime

import feedparser
import httpx

from oss_core.contracts import Cursor, FetchedItem, SourceConfig, SourceItem
from oss_pipeline.connectors.base import RunReport, work_dir
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
    deletes_missing = False  # entries roll off a feed while their articles still exist

    def __init__(self) -> None:
        self.report = RunReport()

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        self.report = report = RunReport()
        urls = [str(u) for u in source.settings.get("urls") or []]
        if not urls:
            raise ValueError("feed connector needs settings.urls")
        max_entries = int(source.settings.get("max_entries", 200))
        fetch_links = bool(source.settings.get("fetch_links", False))
        max_bytes = byte_cap(source.settings, DEFAULT_MAX_BYTES)
        for url in urls:
            refuse_private(url)  # a feed on a private address is a refused source
        tmp = work_dir(source, "feed")
        failed: list[str] = []
        with guarded_client(
            timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as client:
            for url in urls:
                try:
                    resp, feed_body = get_capped(client, url, max_bytes)
                    resp.raise_for_status()
                except (httpx.HTTPError, TooLarge, SourceNotAllowed) as exc:
                    failed.append(f"{url}: {type(exc).__name__}: {exc}")
                    continue
                parsed = feedparser.parse(feed_body)
                if parsed.bozo and not parsed.entries:
                    failed.append(f"{url}: not a feed ({parsed.get('bozo_exception')})")
                    continue
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
                        except (TooLarge, SourceNotAllowed):
                            continue  # over the cap, or on a private address: not indexed
                        except httpx.HTTPError as exc:
                            report.skip(link, f"{type(exc).__name__}: {exc}")
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
                    path = tmp / f"{digest}.html"
                    if not path.exists():
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
        if failed:
            count = f"{len(failed)} of {len(urls)} feeds"
            raise ConnectionError(f"{count} could not be read: {failed[0]}")

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(
            item=item, path=str(item.metadata["path"]), media_type=item.metadata.get("media_type")
        )

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []
