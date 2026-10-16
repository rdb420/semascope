#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.48"]
# ///
"""Browser smoke test of the UI against a running stack with the demo loaded.

Signs in, searches, checks the query chips, clicks a hierarchical facet,
opens a document and checks a tag's tooltip, the knowledge-model pages and a
concept page, the graph (a neighbourhood and a shortest path), accepts and
rejects items on the review page, runs a list search, and fails on any
browser console error. Screenshots land in SEMASCOPE_UI_SHOTS (default: the
current directory); a failure also saves failure.png there.

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

    # a tag's tooltip on the document page names the stage and the confidence
    tags = page.locator("div:has(> h2:has-text('Tags (')) li a")
    expect(tags.first).to_be_visible(timeout=20000)
    tags.first.hover()
    tooltip = tags.first.get_attribute("title") or ""
    if not re.fullmatch(r"[a-z]+ · [01]\.\d\d", tooltip):
        raise AssertionError(f"tag tooltip {tooltip!r} does not read '<stage> · <confidence>'")
    shot(page, "04b-tag-tooltip.png")

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
