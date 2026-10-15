#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6,<7", "httpx>=0.28"]
# ///
"""Precision and recall of pack tagging on the demo corpus against packs/demo/golden.yaml.

Runs against a live Semascope (SEMASCOPE_API_URL, SEMASCOPE_API_TOKEN) with
the demo collection loaded. Document level: for each text, the individuals
and concepts found (from the per-scheme path fields) are compared with the
expectation; "absent" entries count as false positives when found.

Usage: uv run scripts/tag_quality.py [--collection demo]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
ENTITY_SCHEMES = ("deity", "hero", "teacher", "place")


def leaf(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="demo")
    ap.add_argument("--golden", default=str(ROOT / "packs" / "demo" / "golden.yaml"))
    args = ap.parse_args()
    base = os.environ.get("SEMASCOPE_API_URL", "http://localhost:8000").rstrip("/") + "/api"
    token = os.environ.get("SEMASCOPE_API_TOKEN", "")
    golden = yaml.safe_load(Path(args.golden).read_text(encoding="utf-8"))["texts"]
    tp = fp = fn = 0
    rows: list[str] = []
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=60) as c:
        resp = c.post(
            "/search",
            json={"q": "*:*", "collection": args.collection, "page_size": 100, "facets": []},
        )
        resp.raise_for_status()
        hits = resp.json()["hits"]
        for slug, expect in golden.items():
            hit = next((h for h in hits if slug in h["uri"]), None)
            if hit is None:
                rows.append(f"{slug:22} missing from the collection")
                continue
            fields = c.get(f"/documents/{hit['id']}").json()["fields"]
            found: set[str] = set()
            for scheme in ENTITY_SCHEMES:
                found |= {leaf(p) for p in fields.get(f"x_{scheme}_paths") or []}
            for scheme in ("traditions", "text-types", "themes"):
                found |= {leaf(p) for p in fields.get(f"x_{scheme}_paths") or []}
            want = set()
            for key in ("entities", "traditions", "text_types", "themes"):
                want |= set(expect.get(key, []))
            absent = set(expect.get("absent", []))
            got_tp, got_fn, got_fp = want & found, want - found, absent & found
            tp, fn, fp = tp + len(got_tp), fn + len(got_fn), fp + len(got_fp)
            rows.append(
                f"{slug:22} recall {len(got_tp)}/{len(want)}  "
                f"missed={sorted(got_fn)}  wrongly tagged={sorted(got_fp)}"
            )
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    print("\n".join(rows))
    print(f"\nprecision (vs absent lists) {precision:.2f}   recall {recall:.2f}")
    print(f"tp={tp} fp={fp} fn={fn}")
    return 0 if recall >= 0.8 and precision >= 0.9 else 1


if __name__ == "__main__":
    sys.exit(main())
