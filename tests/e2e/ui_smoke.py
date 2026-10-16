#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.48"]
# ///
"""Browser smoke test of the UI against a running stack with the demo loaded.

Signs in, searches, checks the query chips, clicks a hierarchical facet,
opens a document and checks a tag's tooltip, its highlighted occurrences and
its "why this tag" panel, the knowledge-model pages and a concept page, the
graph (a neighbourhood and a shortest path), accepts and rejects items on the
review page, runs a list search, and fails on any browser console error.
It also walks the views of a search: the query highlighted on the document
page with its original file, the table, entities and co-occurrence graph
views (with the documents behind an edge), and the alerts page with a feed.
Screenshots land in SEMASCOPE_UI_SHOTS (default: the current directory); a
failure also saves failure.png there.

    SEMASCOPE_UI_PASSWORD=... uv run tests/e2e/ui_smoke.py
    # first: uv run --with playwright python -m playwright install chromium
    # also honours SEMASCOPE_UI_URL, SEMASCOPE_UI_USER and SEMASCOPE_UI_COLLECTION
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import Page, expect, sync_playwright

BASE = os.environ.get("SEMASCOPE_UI_URL", "https://localhost:8443").rstrip("/")
USER = os.environ.get("SEMASCOPE_UI_USER", "admin")
PASSWORD = os.environ.get("SEMASCOPE_UI_PASSWORD", "")
COLLECTION = os.environ.get("SEMASCOPE_UI_COLLECTION", "demo")
SHOTS = Path(os.environ.get("SEMASCOPE_UI_SHOTS", "."))
DEMO = "https://semascope.dev/ontology/demo#"


def shot(page: Page, name: str, *, full_page: bool = False) -> None:
    page.screenshot(path=str(SHOTS / name), full_page=full_page)


def open_reviews(page: Page) -> int:
    text = page.locator("span", has_text=re.compile(r"^\d+ open$")).first.inner_text()
    return int(text.split()[0])


def decide(page: Page, button: str) -> str:
    """Click a decision button on the first matching review row; wait for the row to go."""

    target = page.locator(button).first
    row = target.locator("xpath=ancestor::li[1]")
    label = row.locator("span.font-medium").inner_text()
    href = row.locator("a").first.get_attribute("href")
    same = (
        page.locator("main ul > li")
        .filter(has=page.locator(f"a[href='{href}']"))
        .filter(has=page.locator("span.font-medium", has_text=re.compile(f"^{re.escape(label)}$")))
    )
    count = same.count()
    target.click()
    expect(same).to_have_count(count - 1, timeout=20000)
    page.wait_for_load_state("networkidle")
    return label


def run(page: Page) -> None:
    page.goto(BASE + "/login", wait_until="networkidle")
    page.fill("input[autocomplete=username]", USER)
    page.fill("input[type=password]", PASSWORD)
    page.click("button:has-text('Sign in')")
    page.wait_for_url(BASE + "/", timeout=15000)
    page.wait_for_selector("text=result", timeout=20000)
    shot(page, "01-search.png")
    page.select_option("select", COLLECTION)
    page.wait_for_timeout(1500)
    page.fill("input[placeholder^='Search']", "Ulysses and Minerva")
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Your query names", timeout=20000)
    shot(page, "02-query.png")
    page.click("button:has-text('greek')")
    page.wait_for_timeout(2000)
    shot(page, "03-facet.png")
    page.click("ol li a >> nth=0")
    page.wait_for_selector("text=Pipeline", timeout=20000)
    shot(page, "04-document.png")
    document_views(page)

    # a tag's tooltip on the document page names the stage, its version and the confidence
    tags = page.locator("div:has(> h2:has-text('Tags (')) li button[aria-pressed]")
    expect(tags.first).to_be_visible(timeout=20000)
    tags.first.hover()
    tooltip = tags.first.get_attribute("title") or ""
    if not re.fullmatch(r"[a-z]+( v\w+)? · [01]\.\d\d( · \S+@\S+)?", tooltip):
        raise AssertionError(f"tag tooltip {tooltip!r} does not read '<stage> v<n> · <confidence>'")
    shot(page, "04b-tag-tooltip.png")
    # selecting it highlights its occurrences and explains it
    tags.first.click()
    expect(page.locator("section[aria-label='Why this tag']")).to_be_visible(timeout=20000)
    expect(page.locator("article mark").first).to_be_visible(timeout=20000)
    expect(page.locator("section[aria-label='Why this tag'] >> text=Method")).to_be_visible()
    shot(page, "04c-why-this-tag.png")

    page.click("text=Knowledge models")
    page.wait_for_selector("text=semascope-demo", timeout=20000)
    shot(page, "05-packs.png")
    page.click("a:has-text('Sacred texts and mythology demo model')")
    page.wait_for_selector("text=Competency questions", timeout=20000)
    page.click("button:has-text('Traditions')")
    page.wait_for_timeout(1500)
    shot(page, "06-pack.png", full_page=True)
    page.click("a:has-text('Greek')")
    page.wait_for_selector("text=Documents (", timeout=20000)
    shot(page, "07-concept.png")
    page.click("text=Graph")
    page.wait_for_selector("text=connect the most documents", timeout=20000)
    page.click("ul button >> nth=0")
    page.wait_for_selector("text=nodes", timeout=30000)
    page.wait_for_timeout(2000)
    shot(page, "08-graph.png")
    edge_documents(page)

    # a shortest path between two demo entities on the graph page
    query = urlencode({"id": f"Entity:{DEMO}odysseus", "collection": COLLECTION})
    page.goto(f"{BASE}/graph?{query}", wait_until="networkidle")
    page.wait_for_selector("text=nodes", timeout=30000)
    target = page.locator("span:has-text('path to') select")
    expect(target.locator("option", has_text="Hector")).to_have_count(1, timeout=20000)
    target.select_option(label="Hector")
    page.click("button:has-text('Find')")
    path = page.locator("p", has_text="shortest path:")
    expect(path).to_be_visible(timeout=30000)
    expect(path).to_contain_text("Odysseus")
    expect(path).to_contain_text("Hector")
    page.wait_for_timeout(1500)
    shot(page, "08b-graph-path.png")

    # accept (link) one review item and reject another
    page.click("text=Review")
    page.wait_for_selector("text=Review queue", timeout=20000)
    expect(page.locator("ul > li").first).to_be_visible(timeout=20000)
    shot(page, "09-review.png")
    before = open_reviews(page)
    if before < 2:
        raise AssertionError(f"need two open review items, found {before}")
    accepted = decide(
        page, "li button:has-text('Link'):enabled, li button:has-text('Accept'):enabled"
    )
    expect(page.locator("span", has_text=f"{before - 1} open")).to_be_visible(timeout=20000)
    rejected = decide(page, "li button:has-text('Reject'):enabled")
    expect(page.locator("span", has_text=f"{before - 2} open")).to_be_visible(timeout=20000)
    print(f"review: accepted {accepted!r}, rejected {rejected!r} ({before} -> {before - 2} open)")
    shot(page, "09b-review-decided.png")

    page.click("text=Map")
    page.wait_for_selector("text=places from the active packs", timeout=20000)
    page.wait_for_timeout(3000)
    shot(page, "10-map.png")
    page.goto(BASE + "/?q=flood&view=terms&collection=" + COLLECTION, wait_until="networkidle")
    page.wait_for_timeout(2000)
    shot(page, "11-terms.png")

    # list search: one phrase query per name, with counts
    page.click("text=Lists")
    page.wait_for_selector("text=Search a list of names", timeout=20000)
    page.fill("textarea", "Odysseus\nArjuna\nXyzzy Nonesuch")
    page.locator("select").last.select_option(COLLECTION)
    page.click("button:has-text('Search')")
    rows = page.locator("tbody tr")
    expect(rows).to_have_count(3, timeout=20000)
    counts = {
        rows.nth(i).locator("td").nth(0).inner_text(): int(
            rows.nth(i).locator("td").nth(1).inner_text()
        )
        for i in range(3)
    }
    if counts.get("Odysseus", 0) < 1 or counts.get("Xyzzy Nonesuch") != 0:
        raise AssertionError(f"unexpected list search counts {counts}")
    shot(page, "12-list.png")

    search_views(page)
    alerts_and_feeds(page)
    extras_pages(page)


def document_views(page: Page) -> None:
    """The query carried from the results: highlighted, counted, and the original opens."""

    if "q=" not in page.url:
        raise AssertionError(f"the result link dropped the query: {page.url}")
    expect(page.locator("[aria-label='Query matches']")).to_contain_text("match", timeout=20000)
    expect(page.locator("article mark[data-tone=query]").first).to_be_visible(timeout=20000)
    shot(page, "04a-query-matches.png")
    href = page.locator("a:has-text('Open original')").get_attribute("href") or ""
    resp = page.request.get(BASE + href)
    if resp.status != 200 or not resp.headers.get("content-disposition", "").startswith("inline"):
        raise AssertionError(f"original: {resp.status} {resp.headers}")


def edge_documents(page: Page) -> None:
    """An edge of the neighbourhood graph lists the documents behind it."""

    page.click("summary:has-text('Edges (')")
    page.locator("details li button").first.click()
    panel = page.locator("section[aria-label='Edge documents']")
    expect(panel.locator("li a").first).to_be_visible(timeout=20000)
    shot(page, "08a-graph-edge.png")


def search_views(page: Page) -> None:
    """Table, entities and graph views of one search, reached from the search page."""

    page.goto(f"{BASE}/?{urlencode({'q': 'Troy'})}", wait_until="networkidle")
    page.select_option("select", COLLECTION)
    page.wait_for_timeout(1500)
    page.click("a:text-is('table')")
    rows = page.locator("tbody tr")
    expect(rows.first).to_be_visible(timeout=20000)
    expect(page.locator("dl[aria-label='Column statistics']")).to_contain_text("Documents")
    page.click("th button:has-text('Size')")
    page.wait_for_url(re.compile(r"sort="), timeout=10000)
    expect(page.locator("th[aria-sort=descending]")).to_contain_text("Size")
    shot(page, "13-table.png")

    page.click("a:text-is('entities')")
    cards = page.locator("main section[aria-label]")
    expect(cards.first).to_be_visible(timeout=20000)
    shot(page, "14-entities.png")
    first = cards.first.locator("li button").first
    first.click()
    page.wait_for_url(re.compile(r"[?&]f="), timeout=10000)
    page.wait_for_selector("text=result", timeout=20000)

    page.click("a:text-is('graph')")
    page.wait_for_selector("text=edges", timeout=30000)
    page.click("summary:has-text('Edges (')")
    page.locator("details li button").first.click()
    expect(page.locator("section[aria-label='Edge documents'] li a").first).to_be_visible(
        timeout=20000
    )
    page.wait_for_timeout(1500)
    shot(page, "15-graph-of-results.png")


def alerts_and_feeds(page: Page) -> None:
    """Save a search, see it on the alerts page, switch its alert on and read its Atom feed."""

    page.goto(f"{BASE}/?{urlencode({'q': 'Odysseus'})}", wait_until="networkidle")
    page.select_option("select", COLLECTION)
    page.wait_for_timeout(1500)
    page.once("dialog", lambda d: d.accept("smoke: Odysseus"))
    page.click("button:has-text('Save search')")
    page.wait_for_timeout(1000)
    page.click("text=Alerts")
    page.wait_for_selector("text=Alerts and feeds", timeout=20000)
    item = page.locator("main li", has=page.locator("a:text-is('smoke: Odysseus')")).first
    expect(item.locator("ol li").first).to_be_visible(timeout=20000)
    box = item.locator("input[type=checkbox]")
    if not box.is_checked():
        box.click()  # controlled: it turns on once the server has saved the alert
        expect(box).to_be_checked(timeout=20000)
    create = page.locator("button:has-text('Create feed token')")
    if create.count():
        create.click()
    atom = item.locator("a:text-is('Atom')")
    expect(atom).to_be_visible(timeout=20000)
    shot(page, "16-alerts.png")
    feed = page.request.get(atom.get_attribute("href") or "")
    body = feed.text()
    if feed.status != 200 or "<entry>" not in body:
        raise AssertionError(f"atom feed: {feed.status} {body[:300]}")


def extras_pages(page: Page) -> None:
    """ADR-0012: similar documents and export links on a document page, the overview page."""

    page.goto(BASE + "/?q=Ulysses&collection=" + COLLECTION, wait_until="networkidle")
    page.click("ol li a >> nth=0")
    panel = page.locator("[data-testid=similar-panel]")
    expect(panel.locator("li a").first).to_be_visible(timeout=30000)
    page.click("[data-testid=similar-panel] button:has-text('Words')")
    expect(panel.locator("li a").first).to_be_visible(timeout=30000)
    expect(page.locator("a:has-text('RDF (Turtle)')")).to_be_visible()
    shot(page, "17-similar.png")
    page.click("header >> text=Overview")
    expect(page.locator("h1", has_text="Overview")).to_be_visible(timeout=20000)
    expect(page.locator("section:has(h2:has-text('Concepts')) li a").first).to_be_visible(
        timeout=20000
    )
    expect(page.locator("link[rel=search]")).to_have_attribute("href", "/api/opensearch.xml")
    shot(page, "18-overview.png", full_page=True)


def main() -> int:
    if not PASSWORD:
        print("set SEMASCOPE_UI_PASSWORD", file=sys.stderr)
        return 2
    SHOTS.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        def on_console(m: object) -> None:
            kind, text = getattr(m, "type", ""), getattr(m, "text", "")
            if kind == "error" and "401" not in text:
                errors.append(f"console.error: {text}")

        page.on("console", on_console)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        try:
            run(page)
        except Exception as exc:  # noqa: BLE001 - report every failure the same way
            errors.append(f"step failed: {type(exc).__name__}: {exc}")
            shot(page, "failure.png", full_page=True)
        browser.close()
    print("errors:", json.dumps(errors, indent=1) if errors else "none")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
