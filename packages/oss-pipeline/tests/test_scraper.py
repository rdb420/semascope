"""Scraper rules: CSS selectors pick a web page's content, title and fields."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from oss_core.contracts import SourceConfig
from oss_pipeline.connectors import web
from oss_pipeline.connectors.web import WebConnector
from oss_pipeline.scraper import ScrapeRuleError, rules_of, scrape

PAGE = b"""<html><head><title>Site | Egrets</title>
<meta name="author" content="Pat Field"></head>
<body><nav><a href="/other.html">Other page</a> Menu words</nav>
<article class="post"><h1 class="headline">Great egrets return</h1>
<p>Forty egrets roost at the lagoon.</p>
<ul class="tags"><li>egrets</li><li>lagoon</li><li>egrets</li></ul>
<div class="advert">Buy binoculars</div></article>
<footer>Copyright footer</footer></body></html>"""

RULES = {
    "remove": [".advert"],
    "content": "article.post",
    "title": "h1.headline",
    "fields": {"author": "meta[name=author] @content", "tags": "ul.tags li"},
}


def test_rules_pick_content_title_and_fields() -> None:
    page, found = scrape(PAGE, "text/html", {"scrape": RULES})
    assert found == {
        "title": "Great egrets return",
        "fields": {"x_author_ss": ["Pat Field"], "x_tags_ss": ["egrets", "lagoon"]},
    }
    text = page.decode()
    assert "Forty egrets" in text and "<title>Great egrets return</title>" in text
    assert "Menu words" not in text and "Copyright footer" not in text
    assert "Buy binoculars" not in text
    assert 'name="semascope-scrape"' in text


def test_without_rules_or_for_other_types_the_page_is_unchanged() -> None:
    assert scrape(PAGE, "text/html", {}) == (PAGE, {})
    assert scrape(b"%PDF", "application/pdf", {"scrape": RULES}) == (b"%PDF", {})


def test_a_rule_change_changes_the_stored_page() -> None:
    first, _ = scrape(PAGE, "text/html", {"scrape": RULES})
    again, _ = scrape(PAGE, "text/html", {"scrape": RULES})
    other, _ = scrape(PAGE, "text/html", {"scrape": {**RULES, "fields": {"tags": "li"}}})
    assert first == again and first != other


@pytest.mark.parametrize(
    "rules",
    [
        "article",
        {"content": "a[["},
        {"fields": {"Bad Name": "p"}},
        {"fields": {"x": "p @"}},
        {"fields": ["p"]},
    ],
)
def test_bad_rules_are_refused(rules: Any) -> None:
    with pytest.raises(ScrapeRuleError):
        rules_of({"scrape": rules})


def test_the_web_connector_applies_the_rules_and_still_crawls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEMASCOPE_ALLOWED_PRIVATE_HOSTS", "birds.test")
    pages = {
        "/index.html": PAGE,
        "/other.html": b"<html><body><article class='post'><p>Other</p></article></body></html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"Content-Type": "text/html"})

    from oss_pipeline.connectors.guard import guarded_client as real

    monkeypatch.setattr(
        web, "guarded_client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    settings = {
        "urls": ["http://birds.test/index.html"],
        "mode": "crawl",
        "respect_robots": False,
        "delay_seconds": 0,
        "scrape": RULES,
    }
    src = SourceConfig(id="w", collection="c", connector="web", settings=settings)
    items = {i.uri: i for i in WebConnector().discover(src, None)}
    assert set(items) == {"http://birds.test/index.html", "http://birds.test/other.html"}
    first = items["http://birds.test/index.html"]
    assert first.metadata["title"] == "Great egrets return"
    assert first.metadata["fields"]["x_tags_ss"] == ["egrets", "lagoon"]
    assert first.metadata["fields"]["x_folder_paths"] == ["birds.test"]
    assert "Menu words" not in Path(first.metadata["path"]).read_text()
