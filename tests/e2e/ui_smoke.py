#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.48"]
# ///
"""Browser smoke test of the UI against a running stack with the demo loaded.

Signs in, searches, checks the query chips, clicks a hierarchical facet,
opens a document, the knowledge-model pages and a concept page, and fails
on any browser console error. Screenshots land in the current directory.

    SEMASCOPE_UI_PASSWORD=... uv run tests/e2e/ui_smoke.py
    # first: uv run --with playwright python -m playwright install chromium
    # also honours SEMASCOPE_UI_URL, SEMASCOPE_UI_USER and SEMASCOPE_UI_COLLECTION
"""

from __future__ import annotations

import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("SEMASCOPE_UI_URL", "https://localhost:8443").rstrip("/")
USER = os.environ.get("SEMASCOPE_UI_USER", "admin")
PASSWORD = os.environ.get("SEMASCOPE_UI_PASSWORD", "")
COLLECTION = os.environ.get("SEMASCOPE_UI_COLLECTION", "demo")


def main() -> int:
    if not PASSWORD:
        print("set SEMASCOPE_UI_PASSWORD", file=sys.stderr)
        return 2
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
        page.goto(BASE + "/login", wait_until="networkidle")
        page.fill("input[autocomplete=username]", USER)
        page.fill("input[type=password]", PASSWORD)
        page.click("button:has-text('Sign in')")
        page.wait_for_url(BASE + "/", timeout=15000)
        page.wait_for_selector("text=result", timeout=20000)
        page.screenshot(path="01-search.png")
        page.select_option("select", COLLECTION)
        page.wait_for_timeout(1500)
        page.fill("input[placeholder^='Search']", "Ulysses and Minerva")
        page.keyboard.press("Enter")
        page.wait_for_selector("text=Your query names", timeout=20000)
        page.screenshot(path="02-query.png")
        page.click("button:has-text('greek')")
        page.wait_for_timeout(2000)
        page.screenshot(path="03-facet.png")
        page.click("ol li a >> nth=0")
        page.wait_for_selector("text=Pipeline", timeout=20000)
        page.screenshot(path="04-document.png")
        page.click("text=Knowledge models")
        page.wait_for_selector("text=semascope-demo", timeout=20000)
        page.screenshot(path="05-packs.png")
        page.click("a:has-text('Sacred texts and mythology demo model')")
        page.wait_for_selector("text=Competency questions", timeout=20000)
        page.click("button:has-text('Traditions')")
        page.wait_for_timeout(1500)
        page.screenshot(path="06-pack.png", full_page=True)
        page.click("a:has-text('Greek')")
        page.wait_for_selector("text=Documents (", timeout=20000)
        page.screenshot(path="07-concept.png")
        page.click("text=Graph")
        page.wait_for_selector("text=connect the most documents", timeout=20000)
        page.click("ul button >> nth=0")
        page.wait_for_selector("text=nodes", timeout=30000)
        page.wait_for_timeout(2000)
        page.screenshot(path="08-graph.png")
        page.click("text=Review")
        page.wait_for_selector("text=Review queue", timeout=20000)
        page.screenshot(path="09-review.png")
        page.click("text=Map")
        page.wait_for_selector("text=places from the active packs", timeout=20000)
        page.wait_for_timeout(3000)
        page.screenshot(path="10-map.png")
        page.goto(BASE + "/?q=flood&view=terms&collection=" + COLLECTION, wait_until="networkidle")
        page.wait_for_timeout(2000)
        page.screenshot(path="11-terms.png")
        browser.close()
    print("errors:", json.dumps(errors, indent=1) if errors else "none")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
