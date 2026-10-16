#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Performance baseline against a running Semascope: throughput, stage times, query latency.

Measures 1 and 4 of docs/assess/benchmarks.md, plus the hardware block every result names.

- Throughput: documents and MB per minute from the collection's first document to its
  last `indexed_at`; per-stage median, p95 and maximum from every document's stage runs; and
  the total stage time per media type (what OCR and the office formats cost).
- Query latency: five fixed queries in each mode (keyword, semantic, hybrid), `--runs`
  times each, without and with two facet filters (the top value of two facets, preferring
  the packs' scheme facets). API time (`took_ms`, what Solr and the server measured) and end
  to end (through Caddy, from the host). The first query of each mode is reported apart
  (cold: it loads the query model and warms Solr's caches).

Runs on the Docker host next to the stack; stage data comes from PostgreSQL through
`docker compose exec`, queries through the API:

    uv run scripts/bench.py --project sem-bench --env-file .bench/sem-bench.env \\
        --collection demo --runs 20 --corpus-manifest packs/demo/corpus.sha256
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchlib import (  # noqa: E402
    add_stack_args,
    api_from_args,
    compose_from_args,
    hardware_block,
    human_bytes,
    pct,
    table,
)

QUERIES = [
    "flood",
    "hero returns home after the war",
    "law and righteousness",
    "Zeus",
    "sacrifice to the gods",
]
MODES = ("keyword", "semantic", "hybrid")
DEFAULT_FACETS = ("content_type_group_s", "language_s", "media_type_s", "source_id_s")


def q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def throughput(c: Any, collection: str) -> str:
    rows = c.psql(
        "SELECT d.status, count(*), coalesce(sum(d.size), 0), "
        "extract(epoch FROM min(d.created_at)), extract(epoch FROM max(d.indexed_at)) "
        "FROM catalogue_document d JOIN catalogue_collection c ON c.id = d.collection_id "
        f"WHERE c.slug = {q(collection)} GROUP BY d.status"
    )
    by_status = {r[0]: r for r in rows}
    indexed = by_status.get("indexed")
    if not indexed:
        return f"no indexed documents in {collection}"
    n, size = int(indexed[1]), int(indexed[2])
    start = min(float(r[3]) for r in rows if r[3])
    end = float(indexed[4])
    minutes = max((end - start) / 60, 1e-9)
    others = ", ".join(f"{k} {v[1]}" for k, v in sorted(by_status.items()) if k != "indexed")
    out = [
        table(
            ["documents", "originals", "wall time", "docs/min", "MB/min", "not indexed"],
            [
                [
                    n,
                    human_bytes(size),
                    f"{minutes:.1f} min",
                    f"{n / minutes:.1f}",
                    f"{size / 1e6 / minutes:.2f}",
                    others or "none",
                ]
            ],
        ),
        "",
        "Wall time runs from the first document's discovery to the last `indexed_at` "
        "(stage runs are those of each document's latest processing).",
    ]
    runs = c.psql(
        "SELECT d.id, d.media_type, coalesce(d.metadata->>'ocr', ''), "
        "coalesce(d.metadata->>'page_count', '0'), r->>'stage', (r->>'duration_ms')::int, "
        "r->>'outcome' FROM catalogue_document d JOIN catalogue_collection c "
        "ON c.id = d.collection_id, jsonb_array_elements(d.stage_runs) r "
        f"WHERE c.slug = {q(collection)}"
    )
    per_stage: dict[str, list[float]] = defaultdict(list)
    failed: dict[str, int] = defaultdict(int)
    per_doc: dict[str, float] = defaultdict(float)
    media: dict[str, str] = {}
    order: list[str] = []
    ocr_pages: list[tuple[float, int]] = []  # (extract ms, pages) of OCRed documents
    for doc_id, media_type, ocr, pages, stage, ms, outcome in runs:
        if stage not in order:
            order.append(stage)
        per_stage[stage].append(float(ms))
        failed[stage] += outcome == "failed"
        per_doc[doc_id] += float(ms)
        media[doc_id] = (media_type.split(";")[0] or "unknown") + (
            " (OCR)" if ocr == "true" else ""
        )
        if ocr == "true" and stage == "extract" and int(pages or 0) > 0:
            ocr_pages.append((float(ms), int(pages)))
    out += [
        "",
        table(
            ["stage", "runs", "failed", "median ms", "p95 ms", "max ms"],
            [
                [
                    s,
                    len(per_stage[s]),
                    failed[s],
                    f"{statistics.median(per_stage[s]):.0f}",
                    f"{pct(per_stage[s], 95):.0f}",
                    f"{max(per_stage[s]):.0f}",
                ]
                for s in order
            ],
        ),
    ]
    by_media: dict[str, list[float]] = defaultdict(list)
    for doc_id, total in per_doc.items():
        by_media[media[doc_id]].append(total)
    out += [
        "",
        table(
            ["media type", "docs", "median s per doc", "p95 s", "max s"],
            [
                [
                    m,
                    len(v),
                    f"{statistics.median(v) / 1000:.1f}",
                    f"{pct(v, 95) / 1000:.1f}",
                    f"{max(v) / 1000:.1f}",
                ]
                for m, v in sorted(by_media.items(), key=lambda kv: -statistics.median(kv[1]))
            ],
        ),
    ]
    if ocr_pages:
        per_page = [ms / pages for ms, pages in ocr_pages]
        out += [
            "",
            f"OCR: {len(ocr_pages)} documents, {sum(p for _, p in ocr_pages)} pages; extraction "
            f"{statistics.median(per_page) / 1000:.2f} s per page (median), "
            f"{pct(per_page, 95) / 1000:.2f} s (p95).",
        ]
    return "\n".join(out)


def pick_filters(api: Any, collection: str) -> dict[str, list[str]]:
    """Two facet filters for the filtered runs: the top value of two facets, scheme facets first."""

    body = api.post("/search", json={"q": "*:*", "collection": collection, "page_size": 0}).json()
    total = int(body.get("total", 0))
    facets = body.get("facets", {})
    ranked = sorted(
        (f for f, values in facets.items() if values),
        key=lambda f: (f in DEFAULT_FACETS, not f.endswith("_paths"), f),
    )
    chosen: dict[str, list[str]] = {}
    for field in ranked:
        values = facets[field]
        selective = [v for v in values if 0 < v["count"] < total] or values
        chosen[field] = [selective[0]["value"]]
        if len(chosen) == 2:
            break
    return chosen


def latency(api: Any, collection: str, runs: int) -> str:
    filters = pick_filters(api, collection)
    results: list[list[Any]] = []
    cold: list[list[Any]] = []
    summary: dict[tuple[str, bool], list[tuple[float, float]]] = defaultdict(list)
    for mode in MODES:
        t0 = time.perf_counter()
        resp = api.post("/search", json={"q": QUERIES[0], "collection": collection, "mode": mode})
        elapsed = (time.perf_counter() - t0) * 1000
        resp.raise_for_status()
        cold.append([mode, QUERIES[0], int(resp.json().get("took_ms", 0)), f"{elapsed:.0f}"])
        for filtered in (False, True):
            for query in QUERIES:
                api_ms: list[float] = []
                e2e_ms: list[float] = []
                hits = 0
                for _ in range(runs):
                    payload = {
                        "q": query,
                        "collection": collection,
                        "mode": mode,
                        "page_size": 10,
                        "filters": filters if filtered else {},
                    }
                    t0 = time.perf_counter()
                    resp = api.post("/search", json=payload)
                    elapsed = (time.perf_counter() - t0) * 1000
                    resp.raise_for_status()
                    body = resp.json()
                    api_ms.append(float(body.get("took_ms", 0)))
                    e2e_ms.append(elapsed)
                    hits = int(body.get("total", 0))
                results.append(
                    [
                        mode,
                        "yes" if filtered else "no",
                        query,
                        f"{pct(api_ms, 50):.0f}",
                        f"{pct(api_ms, 95):.0f}",
                        f"{pct(e2e_ms, 50):.0f}",
                        f"{pct(e2e_ms, 95):.0f}",
                        f"{max(e2e_ms, default=0):.0f}",
                        hits,
                    ]
                )
                summary[(mode, filtered)] += list(zip(api_ms, e2e_ms, strict=True))
    filter_text = "; ".join(f"`{k}` = {v[0]!r}" for k, v in filters.items())
    out = [
        f"Facet filters for the filtered runs: {filter_text or 'none available'}. "
        f"{runs} runs per query; latencies in ms.",
        "",
        table(
            ["mode", "filtered", "p50 api", "p95 api", "p50 e2e", "p95 e2e", "max e2e"],
            [
                [
                    mode,
                    "yes" if filtered else "no",
                    f"{pct([a for a, _ in v], 50):.0f}",
                    f"{pct([a for a, _ in v], 95):.0f}",
                    f"{pct([e for _, e in v], 50):.0f}",
                    f"{pct([e for _, e in v], 95):.0f}",
                    f"{max((e for _, e in v), default=0):.0f}",
                ]
                for (mode, filtered), v in summary.items()
            ],
        ),
        "",
        "First query of each mode (cold):",
        "",
        table(["mode", "query", "api ms", "e2e ms"], cold),
        "",
        "Per query:",
        "",
        table(
            [
                "mode",
                "filtered",
                "query",
                "p50 api",
                "p95 api",
                "p50 e2e",
                "p95 e2e",
                "max e2e",
                "hits",
            ],
            results,
        ),
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_stack_args(ap)
    ap.add_argument("--collection", default="demo")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--corpus-manifest", action="append", default=[], type=Path)
    ap.add_argument("--skip-search", action="store_true")
    ap.add_argument("--skip-stages", action="store_true")
    args = ap.parse_args(argv)
    c = compose_from_args(args)
    print(table(["item", "value"], hardware_block(c, args.corpus_manifest)))
    if not args.skip_stages:
        print(f"\n### Throughput: {args.collection}\n")
        print(throughput(c, args.collection))
    if not args.skip_search:
        print(f"\n### Query latency: {args.collection}\n")
        with api_from_args(args, c) as api:
            print(latency(api, args.collection, args.runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
