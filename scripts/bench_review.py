#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Reviewer throughput (measure 5 of docs/assess/benchmarks.md).

1. Open review items per 1,000 documents, by kind (`/api/curation/reviews/summary`).
2. Decision API latency: up to `--decisions` open items are decided one after another, as a
   curator would, mixing kinds in the queue's order. Tag items alternate accept and reject;
   violations are accepted (dismissed); link items are accepted with their best candidate
   when the item has one, else rejected. Latency is the HTTP round trip of each decision,
   which applies it to PostgreSQL, Solr (atomic update) and Neo4j (CURATED edge) before it
   answers.
3. Decisions per minute end to end: from the first decision to the moment every decided
   document shows its curated tags in Solr (`curated_ss`, after Solr's soft commit) and every
   tag or link decision has its CURATED edge in Neo4j.

The decisions are real: run it on a benchmark collection, not on one you curate.

    uv run scripts/bench_review.py --project sem-bench --env-file .bench/sem-bench.env \\
        --collection mixed-1k --decisions 200
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchlib import add_stack_args, api_from_args, compose_from_args, hardware_block, pct, table  # noqa: E402


def decision_for(item: dict[str, Any], n: int) -> dict[str, Any]:
    """What a benchmark curator decides for a review item (deterministic by position)."""

    kind = item["kind"]
    if kind == "violation":
        return {"decision": "accept", "note": "bench: dismissed"}
    if kind == "link":
        candidates = item.get("payload", {}).get("candidates") or []
        iri = candidates[0].get("iri") if candidates and isinstance(candidates[0], dict) else None
        if iri:
            return {"decision": "accept", "link_to": iri, "note": "bench"}
        return {"decision": "reject", "note": "bench"}
    return {"decision": "accept" if n % 2 == 0 else "reject", "note": "bench"}


def open_items(api: Any, collection: str, limit: int) -> list[dict[str, Any]]:
    resp = api.get(
        "/curation/reviews", params={"collection": collection, "status": "open", "limit": limit}
    )
    resp.raise_for_status()
    return list(resp.json())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_stack_args(ap)
    ap.add_argument("--collection", required=True)
    ap.add_argument("--decisions", type=int, default=200)
    ap.add_argument("--timeout", type=float, default=300, help="seconds to wait for Solr and Neo4j")
    ap.add_argument("--corpus-manifest", action="append", default=[], type=Path)
    args = ap.parse_args(argv)
    c = compose_from_args(args)
    print(table(["item", "value"], hardware_block(c, args.corpus_manifest)))
    with api_from_args(args, c) as api:
        coll = api.get(f"/collections/{args.collection}").json()
        docs = int(coll.get("document_count", 0))
        summary = api.get(
            "/curation/reviews/summary", params={"collection": args.collection}
        ).json()
        print(f"\n### Review queue: {args.collection} ({docs} documents)\n")
        print(
            table(
                ["kind", "open items", "per 1,000 docs"],
                [
                    [k, v, f"{v * 1000 / docs:.0f}" if docs else ""]
                    for k, v in sorted(summary.items())
                ]
                + [
                    [
                        "**all**",
                        sum(summary.values()),
                        f"{sum(summary.values()) * 1000 / docs:.0f}" if docs else "",
                    ]
                ],
            )
        )

        items = open_items(api, args.collection, min(args.decisions, 500))[: args.decisions]
        latencies: dict[str, list[float]] = defaultdict(list)
        errors: dict[str, int] = defaultdict(int)
        decided_docs: set[str] = set()
        edge_keys: set[tuple[str, str]] = set()  # CURATED edges are merged per document and target
        before_edges = int(c.cypher("MATCH ()-[r:CURATED]->() RETURN count(r)")[0][0])
        started = time.perf_counter()
        for n, item in enumerate(items):
            body = decision_for(item, n)
            t0 = time.perf_counter()
            resp = api.post(f"/curation/reviews/{item['id']}/decision", json=body)
            latencies[f"{item['kind']} {body['decision']}"].append(
                (time.perf_counter() - t0) * 1000
            )
            if resp.status_code >= 400:
                errors[item["kind"]] += 1
                continue
            if item["kind"] in ("tag", "link") and item.get("document_id"):
                decided_docs.add(item["document_id"])
                edge_keys.add((item["document_id"], body.get("link_to") or item["target"]))
        edges = len(edge_keys)
        api_done = time.perf_counter() - started

        # end to end: every decided document carries curated_ss in Solr, every edge is in Neo4j
        ids = sorted(decided_docs)
        visible_at = None
        deadline = time.perf_counter() + args.timeout
        while time.perf_counter() < deadline:
            found = 0
            for i in range(0, len(ids), 50):
                chunk = " OR ".join(f'"{d}"' for d in ids[i : i + 50])
                query = urlencode(
                    [("q", "*:*"), ("rows", "0"), ("fq", "curated_ss:*"), ("fq", f"id:({chunk})")]
                )
                res = c.solr(f"documents/select?{query}")
                found += int(res["response"]["numFound"])
            graph = int(c.cypher("MATCH ()-[r:CURATED]->() RETURN count(r)")[0][0]) - before_edges
            if found >= len(ids) and graph >= edges:
                visible_at = time.perf_counter() - started
                break
            time.sleep(0.5)

    all_ms = [v for vs in latencies.values() for v in vs]
    print(f"\n### Decisions: {len(all_ms)} on {args.collection}\n")
    print(
        table(
            ["kind and decision", "n", "p50 ms", "p95 ms", "max ms"],
            [
                [k, len(v), f"{pct(v, 50):.0f}", f"{pct(v, 95):.0f}", f"{max(v):.0f}"]
                for k, v in sorted(latencies.items())
            ]
            + [
                [
                    "**all**",
                    len(all_ms),
                    f"{pct(all_ms, 50):.0f}",
                    f"{pct(all_ms, 95):.0f}",
                    f"{max(all_ms, default=0):.0f}",
                ]
            ],
        )
    )
    if errors:
        print("\nerrors by kind: " + ", ".join(f"{k} {v}" for k, v in errors.items()))
    rate_api = len(all_ms) / api_done * 60 if api_done else 0
    print(
        f"\nDecisions through the API: {len(all_ms)} in {api_done:.1f} s, "
        f"{rate_api:.0f} per minute (one curator, sequential)."
    )
    if visible_at is None:
        print(f"Not all decisions were visible in Solr and Neo4j within {args.timeout:.0f} s.")
    else:
        print(
            f"Applied end to end ({len(ids)} documents curated in Solr, {edges} CURATED edges in "
            f"Neo4j) {visible_at:.1f} s after the first decision: "
            f"{len(all_ms) / visible_at * 60:.0f} decisions per minute."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
