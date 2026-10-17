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
views (with the documents behind an edge), and the alerts page with a feed,
the analysis views (clusters, comparison, word tree), a diff of two documents, why
a hit ranked, the synonyms page, the export page and the annotation import (checked,
not imported), then the pack import panel and the bulk tagging form (counted, not run), and
the search page's own controls: title suggestions, sorting, the match and
word-form options, leaving a facet value out, a date range and a query that
is a number.
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
    analysis_pages(page)
    curation_jobs(page)
    search_features(page)


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


def analysis_pages(page: Page) -> None:
    """ADR-0012, the rest: clusters, comparison and word tree of a search, a diff from the
    similar-documents panel, why a hit ranked, synonyms, and the exports and annotation import
    (checked, not imported, so the demo stays as it is)."""

    page.goto(f"{BASE}/analysis?{urlencode({'q': '*:*', 'collection': COLLECTION})}")
    expect(page.locator("article[aria-label^='Cluster'] li a").first).to_be_visible(timeout=60000)
    shot(page, "21-clusters.png", full_page=True)
    page.click("[role=tab]:has-text('Compare')")
    page.fill("#vs", "Arjuna")
    page.click("form[aria-label='Compare with'] button:has-text('Compare')")
    more = page.locator("section[aria-label='More typical of this search'] li")
    expect(more.first).to_be_visible(timeout=60000)
    page.click("[role=tab]:has-text('Word tree')")
    page.fill("#word", "son of")
    page.click("button:has-text('Grow')")
    expect(page.locator("section[aria-label=Tree] ul ul li").first).to_be_visible(timeout=60000)
    shot(page, "22-word-tree.png", full_page=True)

    page.goto(f"{BASE}/?{urlencode({'q': 'Ulysses', 'collection': COLLECTION})}")
    page.click("ol li a >> nth=0")
    diff = page.locator("[data-testid=similar-panel] a:has-text('diff')").first
    expect(diff).to_be_visible(timeout=30000)
    diff.click()
    expect(page.locator("[aria-label='Diff summary']")).to_contain_text("alike", timeout=120000)
    expect(page.locator("section[aria-label=Differences] [data-op]").first).to_be_visible()
    shot(page, "23-diff.png")

    page.goto(f"{BASE}/?{urlencode({'q': 'Odysseus', 'collection': COLLECTION})}")
    page.click("ol li a:has-text('why?') >> nth=0")
    keyword = page.locator("section[aria-label='Keyword ranking']")
    expect(keyword).to_contain_text("Rank #", timeout=30000)
    expect(keyword.locator("table[aria-label='Matching clauses'] tr").nth(1)).to_be_visible()
    shot(page, "24-explain.png", full_page=True)

    page.click("header >> text=Synonyms")
    page.fill("input[aria-label=Query]", "Ulysses")
    page.click("button:has-text('Show expansion')")
    expansion = page.locator("section[aria-label=Expansion]")
    expect(expansion).to_contain_text("Odysseus", timeout=30000)
    expansion.locator("button:has-text('Suggest labels')").first.click()
    expect(page.locator("section[aria-label='Label suggestions']")).to_contain_text(
        "Tagged in", timeout=60000
    )
    shot(page, "25-synonyms.png", full_page=True)

    page.goto(f"{BASE}/export?{urlencode({'q': '*:*', 'collection': COLLECTION})}")
    search = page.locator("section[aria-label='Export this search']")
    expect(search.locator("button:has-text('Parquet')").first).to_be_visible(timeout=20000)
    with page.expect_download(timeout=60000) as download:
        search.locator("button:has-text('Parquet')").first.click()
    if download.value.suggested_filename != "semascope-documents.parquet":
        raise AssertionError(f"unexpected download {download.value.suggested_filename}")
    notes = SHOTS / "smoke-annotations.json"
    notes.write_text(
        json.dumps({"type": "Annotation", "bodyValue": "x", "target": "https://nowhere.example/"})
    )
    importer = page.locator("section[aria-label='Import annotations']")
    importer.locator("input[type=file]").set_input_files(str(notes))
    importer.locator("button:has-text('Check')").click()
    expect(importer.locator("[aria-label='Import result']")).to_contain_text(
        "Would import 0 of 1", timeout=20000
    )
    shot(page, "26-export.png", full_page=True)


def curation_jobs(page: Page) -> None:
    """The pack import panel and its job list (editors), and the concept page's bulk tagging
    form, counted but not run, so the demo stays as it is."""

    page.goto(BASE + "/packs", wait_until="networkidle")
    expect(page.locator("form[aria-label='Import a pack']")).to_be_visible(timeout=20000)
    expect(page.locator("h2", has_text="Pack jobs")).to_be_visible()
    page.click("form[aria-label='Import a pack'] button:has-text('Plain list')")
    expect(page.locator("textarea[aria-label='Names']")).to_be_visible()
    shot(page, "19-pack-import.png", full_page=True)
    greek = "https://semascope.dev/ontology/demo/traditions#greek"
    page.goto(f"{BASE}/concepts?{urlencode({'iri': greek})}", wait_until="networkidle")
    form = page.locator("form[aria-label='Tag the documents of a search']")
    expect(form).to_be_visible(timeout=20000)
    form.locator("select[aria-label='Collection']").select_option(COLLECTION)
    form.locator("button:has-text('Count documents')").click()
    accept = form.locator("button", has_text=re.compile(r"^Accept on \d+ documents?$"))
    expect(accept).to_be_visible(timeout=20000)
    shot(page, "20-bulk-tag.png")


def go(page: Page, **params: str) -> None:
    page.goto(f"{BASE}/?{urlencode(params)}", wait_until="networkidle")


def search_features(page: Page) -> None:
    """WS-4: suggestions, sort, options, an excluded facet value, dates, a numeric query."""

    # a query that is a number survives the router's search-param parsing
    go(page, q="1984", collection=COLLECTION)
    expect(page.locator("input[role=combobox]")).to_have_value("1984")
    expect(page.locator("text=/\\d+ results?/").first).to_be_visible(timeout=20000)

    # title suggestions while typing; Enter on one searches for that title
    go(page, collection=COLLECTION)
    box = page.locator("input[role=combobox]")
    box.fill("odys")
    option = page.locator("[role=listbox] [role=option]").first
    expect(option).to_be_visible(timeout=20000)
    shot(page, "21-suggest.png")
    box.press("ArrowDown")
    box.press("Enter")
    page.wait_for_url(re.compile(r"q=%22"), timeout=10000)
    expect(page.locator("ol li").first).to_be_visible(timeout=20000)

    # a search without words is newest first; sort and the options go into the URL
    go(page, collection=COLLECTION)
    expect(page.locator("section p", has_text="newest first")).to_be_visible(timeout=20000)
    page.select_option("select[aria-label=Sort]", "title")
    page.wait_for_url(re.compile(r"sort=title"), timeout=10000)
    page.select_option("select[aria-label=Match]", "any")
    page.wait_for_url(re.compile(r"op=any"), timeout=10000)
    page.locator("label:has-text('other word forms') input").uncheck()
    page.wait_for_url(re.compile(r"stem=false"), timeout=10000)

    # leave a facet value out, see it in the filters box, bring it back
    go(page, q="Troy", collection=COLLECTION)
    facet = page.locator("aside[aria-label=Facets] li.group").first
    expect(facet).to_be_visible(timeout=20000)
    facet.hover()
    facet.locator("button[aria-label^='leave out']").click()
    page.wait_for_url(re.compile(r"[?&]x="), timeout=10000)
    chips = page.locator("[aria-label='Filters in force']")
    expect(chips).to_contain_text("not ")
    shot(page, "22-excluded.png")
    chips.locator("button[aria-label^='remove not']").click()
    expect(chips).to_have_count(0, timeout=10000)

    # a date range from the histogram, shown as a filter with a way back to all dates
    go(page, collection=COLLECTION, **{"from": "2000-01-01", "to": "2100-01-01"})
    expect(page.locator("[aria-label='Filters in force']")).to_contain_text("Modified")
    expect(page.locator("button:has-text('all dates')")).to_be_visible(timeout=20000)
    page.click("button:has-text('all dates')")
    page.wait_for_url(re.compile(r"^(?!.*from=).*$"), timeout=10000)

    # list search reports how each name was matched, sortable by score
    page.click("text=Lists")
    page.fill("textarea", "Odysseus\nOdyseus")
    page.locator("select").last.select_option(COLLECTION)
    page.click("button:has-text('Search')")
    rows = page.locator("tbody tr")
    expect(rows).to_have_count(2, timeout=20000)
    expect(page.locator("tbody")).to_contain_text("near spelling")
    page.click("th button:has-text('Best score')")
    expect(page.locator("th[aria-sort=descending]")).to_contain_text("Best score")
    shot(page, "23-list-methods.png")


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
