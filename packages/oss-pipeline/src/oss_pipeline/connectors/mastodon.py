"""Mastodon connector: public posts of a Mastodon (or compatible ActivityPub) server.

Settings:

- `instance` (required): the server, `https://mastodon.social`;
- `hashtags`: tags whose public timelines are read (`["birding", "wetlands"]`);
- `accounts`: accounts whose public posts are read (`["@audubon"]`, `["user@other.server"]`);
- `timeline`: `local` or `federated` for the server's public timeline;
- `token`: an access token, for servers that show timelines only to signed-in users (the API
  masks it when a source is read back);
- `max_statuses` (200 per timeline), `max_bytes` (per response).

Each post is a document at its own URL, dated when it was posted (or last edited); its text
is the post's text (its HTML reduced to text) with its content warning and the descriptions
of its images. A boost is indexed as
the post it boosts. `author_s` is the author's address, `mastodon_tags_ss` the post's
hashtags, `in_reply_to_s` the post it answers. Like a feed, a timeline shows its latest
posts only, so posts that roll off are not deleted (unless the source sets
`delete_missing: true`). A timeline that cannot be read is reported; when none can be read,
the run fails. Every request goes through the outbound guard. X (Twitter) is out of scope:
its API is not open to read without a paid agreement.
"""

from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlsplit

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

PAGE = 40  # the most a Mastodon timeline returns per request
MAX_STATUSES = 200


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


class _Text(HTMLParser):
    """The text of a post's HTML, paragraphs and line breaks kept; no markup survives."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("br", "p") and self.parts:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def text_of(markup: str) -> str:
    parser = _Text()
    parser.feed(markup)
    parser.close()
    return "".join(parser.parts).strip()


def render(status: dict[str, Any]) -> bytes:
    """A post as a small HTML page: author, content warning, text, image descriptions.

    The server's HTML is reduced to its text and escaped: nothing a remote server sends is
    kept as markup."""

    account = status.get("account") if isinstance(status.get("account"), dict) else {}
    author = str((account or {}).get("display_name") or (account or {}).get("acct") or "")
    title = f"Post by {author}" if author else "Post"
    parts = [f"<html><head><title>{html.escape(title)}</title></head><body>"]
    if status.get("spoiler_text"):
        parts.append(f"<p>{html.escape(str(status['spoiler_text']))}</p>")
    for paragraph in text_of(str(status.get("content") or "")).split("\n"):
        if paragraph.strip():
            parts.append(f"<p>{html.escape(paragraph.strip())}</p>")
    for media in status.get("media_attachments") or []:
        if isinstance(media, dict) and media.get("description"):
            parts.append(f"<p>{html.escape(str(media['description']))}</p>")
    parts.append("</body></html>")
    return "\n".join(parts).encode()


class MastodonConnector:
    name = "mastodon"
    deletes_missing = False  # posts roll off a timeline while they still exist

    def __init__(self) -> None:
        self.report = RunReport()

    def _timelines(
        self, client: httpx.Client, base: str, settings: dict[str, Any], max_bytes: int
    ) -> list[tuple[str, str, dict[str, str]]]:
        """(label, API path, query) of each timeline to read."""

        out: list[tuple[str, str, dict[str, str]]] = []
        for tag in settings.get("hashtags") or []:
            name = str(tag).lstrip("#").strip()
            if name:
                out.append((f"#{name}", f"/api/v1/timelines/tag/{quote(name, safe='')}", {}))
        timeline = str(settings.get("timeline") or "")
        if timeline in ("local", "federated"):
            query = {"local": "true"} if timeline == "local" else {}
            out.append((f"{timeline} timeline", "/api/v1/timelines/public", query))
        for acct in settings.get("accounts") or []:
            handle = str(acct).lstrip("@").strip()
            if not handle:
                continue
            try:
                resp, body = get_capped(
                    client, f"{base}/api/v1/accounts/lookup", max_bytes, params={"acct": handle}
                )
                resp.raise_for_status()
                account_id = str(json.loads(body)["id"])
            except (httpx.HTTPError, TooLarge, SourceNotAllowed, ValueError, KeyError) as exc:
                self.report.skip(f"@{handle}", f"{type(exc).__name__}: {exc}")
                continue
            path = f"/api/v1/accounts/{quote(account_id, safe='')}/statuses"
            out.append((f"@{handle}", path, {"exclude_replies": "false"}))
        return out

    def _statuses(
        self,
        client: httpx.Client,
        base: str,
        path: str,
        query: dict[str, str],
        limit: int,
        max_bytes: int,
    ) -> Iterator[dict[str, Any]]:
        """A timeline's posts, newest first, paging back with `max_id` up to `limit`."""

        max_id: str | None = None
        taken = 0
        while taken < limit:
            params = {**query, "limit": str(min(PAGE, limit - taken))}
            if max_id:
                params["max_id"] = max_id
            resp, body = get_capped(client, f"{base}{path}", max_bytes, params=params)
            resp.raise_for_status()
            page = json.loads(body)
            if not isinstance(page, list) or not page:
                return
            for status in page:
                if isinstance(status, dict):
                    taken += 1
                    yield status
            max_id = str(page[-1].get("id") or "") if isinstance(page[-1], dict) else ""
            if not max_id or len(page) < int(params["limit"]):
                return

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        self.report = report = RunReport()
        settings = source.settings
        instance = str(settings.get("instance") or "").rstrip("/")
        if not instance:
            raise ValueError("mastodon connector needs settings.instance")
        if "://" not in instance:
            instance = f"https://{instance}"
        refuse_private(instance)
        limit = int(settings.get("max_statuses", MAX_STATUSES))
        max_bytes = byte_cap(settings, DEFAULT_MAX_BYTES)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if settings.get("token"):
            headers["Authorization"] = f"Bearer {settings['token']}"
        tmp = work_dir(source, "mastodon")
        seen: set[str] = set()
        read = 0
        with guarded_client(timeout=30.0, follow_redirects=False, headers=headers) as client:
            timelines = self._timelines(client, instance, settings, max_bytes)
            if not timelines and not report.unchecked:
                raise ValueError("mastodon connector needs hashtags, accounts or a timeline")
            for label, path, query in timelines:
                try:
                    for status in self._statuses(client, instance, path, query, limit, max_bytes):
                        item = self._item(status, tmp, seen)
                        if item is not None:
                            yield item
                    read += 1
                except (httpx.HTTPError, TooLarge, SourceNotAllowed, ValueError) as exc:
                    report.skip(label, f"{type(exc).__name__}: {exc}")
        if not read:
            first = report.unchecked[0] if report.unchecked else "nothing to read"
            raise ConnectionError(f"no timeline of {instance} could be read: {first}")

    def _item(self, status: dict[str, Any], tmp: Any, seen: set[str]) -> SourceItem | None:
        if isinstance(status.get("reblog"), dict):
            status = status["reblog"]  # a boost: the post it boosts
        url = str(status.get("url") or status.get("uri") or "")
        if not url or url in seen or status.get("visibility") not in (None, "public", "unlisted"):
            return None
        seen.add(url)
        content = render(status)
        digest = hashlib.sha256(content).hexdigest()
        path = tmp / f"{digest}.html"
        if not path.exists():
            path.write_bytes(content)
        account = status.get("account") if isinstance(status.get("account"), dict) else {}
        acct = str((account or {}).get("acct") or "")
        if acct and "@" not in acct:
            acct = f"{acct}@{urlsplit(url).hostname or ''}"
        fields: dict[str, Any] = {}
        if acct:
            fields["x_author_s"] = acct[:300]
        tags = [
            str(t.get("name"))[:100]
            for t in status.get("tags") or []
            if isinstance(t, dict) and t.get("name")
        ]
        if tags:
            fields["x_mastodon_tags_ss"] = tags[:50]
        if status.get("in_reply_to_id"):
            fields["x_in_reply_to_s"] = str(status["in_reply_to_id"])[:100]
        return SourceItem(
            uri=url,
            content_hash=digest,
            modified_at=_when(status.get("edited_at")) or _when(status.get("created_at")),
            size=len(content),
            metadata={
                "path": str(path),
                "media_type": "text/html",
                "title": f"Post by {acct}" if acct else None,
                "fields": fields,
            },
        )

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(
            item=item, path=str(item.metadata["path"]), media_type=item.metadata.get("media_type")
        )

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []
