#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Performance baseline against a running Semascope: search latency, indexing throughput.

Each query runs N times in every mode; p50/p95 latency is reported as the
API measured it (took_ms) and end to end. Stage durations of the collection's
documents give indexing throughput. Output is a Markdown table.

    uv run scripts/bench.py --collection demo --runs 20
"""

from __future__ import annotations

import argparse
import os
import statistics
import time

import httpx

QUERIES = [
    "flood",
    "hero returns home after the war",
    "law and righteousness",
    "Zeus",
    "sacrifice to the gods",
]


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = max(0, min(len(values) - 1, round((p / 100) * (len(values) - 1))))
    return values[k]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="demo")
    ap.add_argument("--runs", type=int, default=10)
    args = ap.parse_args()
    base = os.environ.get("SEMASCOPE_API_URL", "http://localhost:8000").rstrip("/") + "/api"
    token = os.environ.get("SEMASCOPE_API_TOKEN", "")
    rows = [
        "| mode | query | p50 ms (api) | p95 ms (api) | p95 ms (e2e) | hits |",
        "|---|---|---|---|---|---|",
    ]
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=base, headers=headers, timeout=120) as c:
        for mode in ("keyword", "semantic", "hybrid"):
            for q in QUERIES:
                api_ms: list[float] = []
                e2e_ms: list[float] = []
                hits = 0
                for _ in range(args.runs):
                    t0 = time.perf_counter()
                    payload = {"q": q, "collection": args.collection, "mode": mode, "page_size": 10}
                    r = c.post("/search", json=payload)
                    e2e_ms.append((time.perf_counter() - t0) * 1000)
                    body = r.json()
                    api_ms.append(float(body.get("took_ms", 0)))
                    hits = int(body.get("total", 0))
                rows.append(
                    f"| {mode} | {q} | {pct(api_ms, 50):.0f} | {pct(api_ms, 95):.0f} "
                    f"| {pct(e2e_ms, 95):.0f} | {hits} |"
                )
        listing = {"q": "*:*", "collection": args.collection, "page_size": 100, "facets": []}
        docs = c.post("/search", json=listing).json()["hits"]
        per_stage: dict[str, list[float]] = {}
        sizes: list[int] = []
        for h in docs:
            d = c.get(f"/documents/{h['id']}").json()
            sizes.append(int(d.get("size") or 0))
            for r in d["stage_runs"]:
                per_stage.setdefault(r["stage"], []).append(float(r["duration_ms"]))
    print("\n".join(rows))
    print("\n| stage | docs | median ms | p95 ms |\n|---|---|---|---|")
    for stage, values in per_stage.items():
        print(
            f"| {stage} | {len(values)} | {statistics.median(values):.0f} | {pct(values, 95):.0f} |"
        )
    total_ms = sum(sum(v) for v in per_stage.values())
    if total_ms and sizes:
        mb = sum(sizes) / 1e6
        print(
            f"\nindexed {len(docs)} documents, {mb:.1f} MB, {total_ms / 1000:.0f} s of stage time:"
        )
        print(f"{mb / (total_ms / 1000):.2f} MB/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
