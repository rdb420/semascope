#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6,<7", "httpx>=0.28"]
# ///
"""Build the Semascope demo corpus from Project Gutenberg.

Reads `sources.yaml`, downloads each text's plain-text UTF-8 edition into
`cache/` (once), strips the Project Gutenberg header and footer and every
mention of the trademark, writes the result to `corpus/<slug>.txt`, and records
`fetched_at` and `sha256` back into `sources.yaml`.

Derived formats (HTML, EPUB, DOCX, ODT, PDF with and without a text layer, page
images, zip, CSV, mbox, Turtle) are produced by `derive.py` in Phase 1, once
the extraction stages exist to consume them.

Usage: uv run packs/demo/build.py [--only slug] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
CORPUS = HERE / "corpus"

START = re.compile(r"^\*\*\* ?START OF (THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.M)
END = re.compile(r"^\*\*\* ?END OF (THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.M)
TRADEMARK = re.compile(r"Project\s+Gutenberg(-tm|™)?", re.I)


def candidate_urls(base: str, pg_id: int) -> list[str]:
    return [
        f"{base}/ebooks/{pg_id}.txt.utf-8",
        f"{base}/files/{pg_id}/{pg_id}-0.txt",
        f"{base}/cache/epub/{pg_id}/pg{pg_id}.txt",
    ]


def fetch(client: httpx.Client, base: str, pg_id: int) -> bytes:
    last: Exception | None = None
    for url in candidate_urls(base, pg_id):
        try:
            resp = client.get(url, follow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 10_000:
                return resp.content
        except httpx.HTTPError as exc:  # try the next layout
            last = exc
        time.sleep(1.0)  # be polite to the archive
    raise RuntimeError(f"could not fetch Project Gutenberg #{pg_id}: {last}")


def strip(text: str) -> str:
    """Remove the licence header and footer and every trademark mention."""

    start = START.search(text)
    end = END.search(text)
    if not start or not end or end.start() <= start.end():
        raise ValueError("start or end marker not found; refusing to ship an unstripped text")
    body = text[start.end() : end.start()]
    body = TRADEMARK.sub("the archive", body)
    return body.strip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--only", help="build one slug")
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    args = ap.parse_args()

    manifest = yaml.safe_load((HERE / "sources.yaml").read_text(encoding="utf-8"))
    base = manifest["project_gutenberg"]["base_url"]
    CACHE.mkdir(exist_ok=True)
    CORPUS.mkdir(exist_ok=True)

    with httpx.Client(timeout=60.0, headers={"User-Agent": "semascope-demo-builder/0.1"}) as client:
        for entry in manifest["texts"]:
            if args.only and entry["slug"] != args.only:
                continue
            raw_path = CACHE / f"pg{entry['id']}.txt"
            if args.force or not raw_path.exists():
                print(f"fetching #{entry['id']} {entry['title']}")
                raw_path.write_bytes(fetch(client, base, int(entry["id"])))
                entry["fetched_at"] = datetime.now(UTC).date().isoformat()
            text = raw_path.read_text(encoding="utf-8", errors="replace")
            body = strip(text)
            out = CORPUS / f"{entry['slug']}.txt"
            out.write_text(body, encoding="utf-8")
            entry["sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
            entry["bytes"] = len(body.encode("utf-8"))
            print(f"  wrote {out.name} ({entry['bytes']:,} bytes)")

    (HERE / "sources.yaml").write_text(
        "# Demo corpus sources. Every text is public domain: each author and translator\n"
        "# died before 1956. Fetched from Project Gutenberg by build.py, which strips the\n"
        "# Project Gutenberg header, footer and trademark as the Project Gutenberg\n"
        "# Licence requires for redistribution without the trademark.\n"
        "# build.py appends `fetched_at` and `sha256` for each entry it downloads.\n"
        + yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=200),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
