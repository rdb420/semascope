"""Hypothesis connector: web annotations from a Hypothesis service as documents.

Settings:

- `group` (a group id), `uri` (the annotated page), `user` (`acct:name@hypothes.is`) and
  `tag`: which annotations, as the service's search API filters them; at least one of
  `group`, `uri` or `user` is required;
- `token`: an API token for private groups (write-only: the API masks it when a source is
  read back);
- `api_url` (`https://api.hypothes.is/api`): another Hypothesis service;
- `max_annotations` (5,000 per run) and `max_bytes` (per response).

Each annotation becomes a document: its URI is the annotation's own page
(`https://hypothes.is/a/<id>`), its modified time when it was last updated, and its
text the quoted passage, the note and the tags. The annotated page is `x_annotates_s`
(a filter: every annotation of a page), the tags `x_annotation_tags_ss`, the group
`x_hypothesis_group_s` and the author `x_annotator_s`. A run lists every annotation the
filters match, oldest change first, so one deleted on the service is deleted here too;
a page of results that cannot be read leaves the run incomplete, and nothing is deleted.
Annotations can also be attached to Semascope's own documents as notes with the one-off
import (`POST /api/annotations/w3c/import`).

Every request goes through the outbound guard: a service on a private address needs
`SEMASCOPE_ALLOWED_PRIVATE_HOSTS`.
"""

from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

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

API_URL = "https://api.hypothes.is/api"
PAGE = 200
MAX_ANNOTATIONS = 5000
FILTERS = ("group", "uri", "user", "tag")
MAX_TEXT = 100_000


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def quotes(row: dict[str, Any]) -> list[str]:
    """The passages an annotation quotes (its targets' TextQuoteSelectors)."""

    out = []
    for target in _as_list(row.get("target")):
        if not isinstance(target, dict):
            continue
        for sel in _as_list(target.get("selector")):
            if isinstance(sel, dict) and sel.get("type") == "TextQuoteSelector":
                exact = str(sel.get("exact") or "").strip()
                if exact:
                    out.append(exact)
    return out


def annotated(row: dict[str, Any]) -> str:
    """The URI of the page an annotation is about."""

    for target in _as_list(row.get("target")):
        if isinstance(target, dict) and isinstance(target.get("source"), str):
            return str(target["source"])
    return str(row.get("uri") or "")


def page_title(row: dict[str, Any]) -> str:
    titles = (
        (row.get("document") or {}).get("title") if isinstance(row.get("document"), dict) else None
    )
    for title in _as_list(titles):
        if isinstance(title, str) and title.strip():
            return title.strip()
    return ""


def render(row: dict[str, Any]) -> bytes:
    """The annotation as a small HTML page: what it is about, the quote, the note, the tags."""

    target = annotated(row)
    title = f"Annotation on {page_title(row) or target or 'a page'}"
    parts = [f"<html><head><title>{html.escape(title)}</title></head><body>"]
    parts.append(f"<h1>{html.escape(title)}</h1>")
    if target:
        parts.append(
            f'<p><a href="{html.escape(target, quote=True)}">{html.escape(target)}</a></p>'
        )
    for quote in quotes(row):
        parts.append(f"<blockquote>{html.escape(quote[:MAX_TEXT])}</blockquote>")
    text = str(row.get("text") or "")[:MAX_TEXT]
    if text:
        parts.append("".join(f"<p>{html.escape(p)}</p>" for p in text.split("\n\n") if p))
    tags = [str(t) for t in _as_list(row.get("tags")) if isinstance(t, str)]
    if tags:
        parts.append(f"<p>Tags: {html.escape(', '.join(tags))}</p>")
    parts.append("</body></html>")
    return "\n".join(parts).encode()


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


class HypothesisConnector:
    name = "hypothesis"
    deletes_missing = True  # a run lists every annotation its filters match

    def __init__(self) -> None:
        self.report = RunReport()

    def _rows(
        self, client: httpx.Client, api: str, params: dict[str, str], limit: int, max_bytes: int
    ) -> Iterator[dict[str, Any]]:
        """Every matching annotation, oldest change first, paging with `search_after`."""

        after: str | None = None
        taken = 0
        while taken < limit:
            query = {**params, "limit": str(min(PAGE, limit - taken)), "sort": "updated"}
            query["order"] = "asc"
            if after:
                query["search_after"] = after
            try:
                resp, body = get_capped(client, f"{api}/search", max_bytes, params=query)
                resp.raise_for_status()
                rows = json.loads(body).get("rows")
                if not isinstance(rows, list):
                    raise ValueError("no rows in the answer")
            except (httpx.HTTPError, TooLarge, SourceNotAllowed, ValueError) as exc:
                if after is None:
                    raise ConnectionError(f"{api}/search: {type(exc).__name__}: {exc}") from exc
                self.report.skip(f"{api}/search after {after}", f"{type(exc).__name__}: {exc}")
                return
            if not rows:
                return
            for row in rows:
                if isinstance(row, dict):
                    taken += 1
                    yield row
            after = str(rows[-1].get("updated") or "") if isinstance(rows[-1], dict) else ""
            if not after or len(rows) < int(query["limit"]):
                return
        self.report.truncated = True  # max_annotations: what lies past it was not looked at

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]:
        self.report = RunReport()
        settings = source.settings
        params = {k: str(settings[k]) for k in FILTERS if settings.get(k)}
        if not {"group", "uri", "user"} & set(params):
            raise ValueError("hypothesis connector needs settings.group, uri or user")
        api = str(settings.get("api_url") or API_URL).rstrip("/")
        refuse_private(api)  # a service on a private address is a refused source
        site = "{0.scheme}://{0.netloc}".format(urlsplit(api)).replace("api.", "", 1)
        limit = int(settings.get("max_annotations", MAX_ANNOTATIONS))
        max_bytes = byte_cap(settings, DEFAULT_MAX_BYTES)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        token = str(settings.get("token") or "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        tmp = work_dir(source, "hypothesis")
        with guarded_client(timeout=30.0, follow_redirects=False, headers=headers) as client:
            for row in self._rows(client, api, params, limit, max_bytes):
                ident = str(row.get("id") or "")
                if not ident or row.get("hidden"):
                    continue  # moderated away on the service
                links = row.get("links") if isinstance(row.get("links"), dict) else {}
                uri = str((links or {}).get("html") or f"{site}/a/{ident}")
                content = render(row)
                digest = hashlib.sha256(content).hexdigest()
                path = tmp / f"{digest}.html"
                if not path.exists():
                    path.write_bytes(content)
                tags = [str(t)[:100] for t in _as_list(row.get("tags")) if isinstance(t, str)]
                fields: dict[str, Any] = {"x_annotates_s": annotated(row)[:2000]}
                if tags:
                    fields["x_annotation_tags_ss"] = tags[:50]
                if row.get("group"):
                    fields["x_hypothesis_group_s"] = str(row["group"])[:200]
                if row.get("user"):
                    fields["x_annotator_s"] = str(row["user"])[:200]
                yield SourceItem(
                    uri=uri,
                    content_hash=digest,
                    modified_at=_when(row.get("updated")) or _when(row.get("created")),
                    size=len(content),
                    metadata={
                        "path": str(path),
                        "media_type": "text/html",
                        "title": f"Annotation on {page_title(row) or annotated(row)}"[:500],
                        "fields": fields,
                    },
                )

    def fetch(self, item: SourceItem) -> FetchedItem:
        return FetchedItem(
            item=item, path=str(item.metadata["path"]), media_type=item.metadata.get("media_type")
        )

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]:
        return []
