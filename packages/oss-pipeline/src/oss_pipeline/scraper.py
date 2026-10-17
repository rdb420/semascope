"""Scraper rules for web sources: CSS selectors that pick a page's content, title and fields.

A web source's `scrape` setting:

    {
      "remove": ["nav", "footer", ".advert"],
      "content": "article.post",
      "title": "h1.headline",
      "fields": {"author": "meta[name=author] @content", "tags": "ul.tags li"}
    }

- `remove`: elements dropped from the page first;
- `content`: the page is reduced to the elements this selector matches (a site's article
  without its menus); when nothing matches, the whole page is kept;
- `title`: the text of the first match is the document's title;
- `fields`: each name fills the custom field `x_<name>_ss` (indexed and faceted as
  `<name>_ss`) with the text of every match, or an attribute's value with ` @attribute`.

Selectors are CSS (soupsieve: levels 1-4 and more). A page with rules is stored as it was
scraped, with the rules' hash in a `<meta>` element, so a change to the rules makes the next
run process the pages again. Only HTML pages are scraped. Limits: 50 fields, 100 values per
field, 1,000 characters per value. A selector that does not parse fails the run.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Mapping
from typing import Any

HTML_TYPES = ("text/html", "application/xhtml+xml")
MAX_FIELDS = 50
MAX_VALUES = 100
MAX_TEXT = 1000
NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
_ATTR = re.compile(r"^(?P<css>.*?)\s+@(?P<attr>[A-Za-z_:][-A-Za-z0-9_:.]*)$")
_SPACE = re.compile(r"\s+")


class ScrapeRuleError(ValueError):
    """A scrape rule that cannot be used."""


def rules_of(settings: Mapping[str, Any]) -> dict[str, Any] | None:
    """The source's scrape rules, checked; None without any."""

    import soupsieve

    rules = settings.get("scrape")
    if not rules:
        return None
    if not isinstance(rules, dict):
        raise ScrapeRuleError("scrape is a mapping of remove, content, title and fields")
    fields = rules.get("fields") or {}
    if not isinstance(fields, dict) or len(fields) > MAX_FIELDS:
        raise ScrapeRuleError(f"scrape.fields is a mapping of at most {MAX_FIELDS} names")
    selectors: list[str] = [str(s) for s in rules.get("remove") or []]
    selectors += [str(rules[k]) for k in ("content", "title") if rules.get(k)]
    for name, selector in fields.items():
        if not NAME.fullmatch(str(name)):
            raise ScrapeRuleError(f"scrape field {name!r}: lower-case letters, digits and _")
        selectors.append(_split(str(selector))[0])
    for selector in selectors:
        try:
            soupsieve.compile(selector)
        except Exception as exc:  # noqa: BLE001 - soupsieve's syntax error, with its position
            raise ScrapeRuleError(f"scrape selector {selector!r}: {exc}".splitlines()[0]) from exc
    return rules


def _split(selector: str) -> tuple[str, str | None]:
    m = _ATTR.match(selector.strip())
    return (m.group("css"), m.group("attr")) if m else (selector.strip(), None)


def _text(element: Any) -> str:
    return _SPACE.sub(" ", element.get_text(" ")).strip()[:MAX_TEXT]


def scrape(
    content: bytes, media_type: str, settings: Mapping[str, Any]
) -> tuple[bytes, dict[str, Any]]:
    """The page as stored, and what the rules found (`fields`, `title`).

    Without rules, or for a page that is not HTML, the page is returned unchanged."""

    rules = rules_of(settings)
    if rules is None or media_type not in HTML_TYPES:
        return content, {}
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    for selector in rules.get("remove") or []:
        for element in soup.select(str(selector)):
            element.decompose()
    found: dict[str, Any] = {"fields": {}}
    for name, selector in (rules.get("fields") or {}).items():
        css, attr = _split(str(selector))
        values = []
        for element in soup.select(css, limit=MAX_VALUES):
            value = str(element.get(attr) or "").strip()[:MAX_TEXT] if attr else _text(element)
            if value and value not in values:
                values.append(value)
        if values:
            found["fields"][f"x_{name}_ss"] = values
    title = soup.select_one(str(rules["title"])) if rules.get("title") else None
    if title is None:
        title = soup.find("title")
    if title is not None and _text(title):
        found["title"] = _text(title)
    parts = soup.select(str(rules["content"])) if rules.get("content") else []
    body = "".join(str(p) for p in parts) if parts else str(soup.body or soup)
    digest = hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:16]
    page = (
        '<html><head><meta charset="utf-8">'
        f"<title>{html.escape(found.get('title', ''))}</title>"
        f'<meta name="semascope-scrape" content="{digest}"></head>'
        f"<body>{body}</body></html>"
    )
    return page.encode("utf-8"), found


def with_scraped(found: Mapping[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """A web item's metadata with what the scrape rules found merged in."""

    if not found:
        return metadata
    out = {**metadata, "fields": {**(metadata.get("fields") or {}), **found.get("fields", {})}}
    if found.get("title"):
        out["title"] = found["title"]
    return out
