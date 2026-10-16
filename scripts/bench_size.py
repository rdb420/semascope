#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Index and graph size of a running Semascope stack, per 1,000 documents (measures 2 and 3).

Reads, through `docker compose exec`:

- Solr: on-disk size and document count of the `documents`, `entities` and `chunks` cores;
- PostgreSQL: database size, the largest tables, and the data directory on disk;
- the blob store (`/data` of the server: originals, previews, uploads, packs);
- Neo4j: node counts by label, relationship counts by type, and the store on disk.

Sizes are totals for the whole stack. To attribute them to one corpus, save a snapshot
before indexing it and pass it as `--baseline`; the tables then show the difference.

    uv run scripts/bench_size.py --project sem-bench --env-file .bench/sem-bench.env \\
        --save reports/size-demo.json [--baseline reports/size-empty.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchlib import add_stack_args, compose_from_args, hardware_block, human_bytes, table  # noqa: E402

SOLR_CORES = ("documents", "entities", "chunks")


def snapshot(c: Any) -> dict[str, Any]:
    """Raw counts and sizes; every number is a total for the stack."""

    docs = c.psql(
        "SELECT status, count(*), coalesce(sum(size), 0) FROM catalogue_document GROUP BY status"
    )
    status = {row[0]: {"count": int(row[1]), "bytes": int(row[2])} for row in docs}
    text_bytes = int(
        c.psql("SELECT coalesce(sum(octet_length(text)), 0) FROM catalogue_document")[0][0]
    )
    solr: dict[str, Any] = {}
    disk = c.du("solr", "/var/solr/data/*")
    for core in SOLR_CORES:
        size = sum(v for k, v in disk.items() if Path(k).name.startswith(f"{core}_"))
        try:
            found = int(c.solr(f"{core}/select?q=*:*&rows=0&wt=json")["response"]["numFound"])
        except (KeyError, ValueError):
            found = -1
        solr[core] = {"bytes": size, "docs": found}
    pg_db = int(c.psql("SELECT pg_database_size('semascope')")[0][0])
    tables = c.psql(
        "SELECT relname, pg_total_relation_size(relid) FROM pg_catalog.pg_statio_user_tables "
        "ORDER BY 2 DESC LIMIT 6"
    )
    pg_dir = sum(c.du("postgres", "/var/lib/postgresql/data").values())
    blobs = c.du("server", "/data/*")
    labels = c.cypher("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) ORDER BY l")
    types = c.cypher("MATCH ()-[r]->() RETURN type(r), count(*) ORDER BY type(r)")
    neo_disk = c.du("neo4j", "/data/databases/neo4j", "/data/transactions/neo4j")
    return {
        "documents": status,
        "text_bytes": text_bytes,
        "solr": solr,
        "postgres": {
            "database_bytes": pg_db,
            "data_dir_bytes": pg_dir,
            "tables": {t: int(n) for t, n in tables},
        },
        "blobs": {Path(k).name: v for k, v in blobs.items()},
        "neo4j": {
            "labels": {k: int(v) for k, v in labels},
            "types": {k: int(v) for k, v in types},
            "store_bytes": sum(neo_disk.values()),
        },
    }


def minus(now: Any, base: Any) -> Any:
    """now - base, recursively over the snapshot's dicts of numbers."""

    if isinstance(now, dict):
        base = base if isinstance(base, dict) else {}
        return {k: minus(v, base.get(k, 0)) for k, v in now.items()}
    if isinstance(now, int | float):
        return now - (base if isinstance(base, int | float) else 0)
    return now


def report(snap: dict[str, Any], label: str) -> str:
    indexed = snap["documents"].get("indexed", {}).get("count", 0)
    per = 1000 / indexed if indexed else 0.0
    source_bytes = snap["documents"].get("indexed", {}).get("bytes", 0)

    def row(name: str, n: int, note: str = "") -> list[str]:
        ratio = f"{n / source_bytes:.2f}" if source_bytes else ""
        return [name, human_bytes(n), human_bytes(n * per) if per else "", ratio, note]

    out = [
        f"**{label}**: {indexed} documents indexed, {human_bytes(source_bytes)} of originals, "
        f"{human_bytes(snap['text_bytes'])} of extracted text; other statuses: "
        + (
            ", ".join(
                f"{k} {v['count']}" for k, v in sorted(snap["documents"].items()) if k != "indexed"
            )
            or "none"
        ),
        "",
    ]
    rows = [
        row(f"Solr `{core}`", v["bytes"], f"{v['docs']} Solr documents")
        for core, v in snap["solr"].items()
    ]
    pg = snap["postgres"]
    top = ", ".join(f"{t} {human_bytes(n)}" for t, n in list(pg["tables"].items())[:3])
    rows.append(row("PostgreSQL database", pg["database_bytes"], f"largest: {top}"))
    rows.append(row("PostgreSQL data directory", pg["data_dir_bytes"], "database plus WAL"))
    blobs = snap["blobs"]
    rows.append(
        row(
            "Blob store (`/data`)",
            sum(blobs.values()),
            ", ".join(f"{k} {human_bytes(v)}" for k, v in sorted(blobs.items()) if v),
        )
    )
    rows.append(row("Neo4j store", snap["neo4j"]["store_bytes"]))
    total = (
        sum(v["bytes"] for v in snap["solr"].values())
        + pg["data_dir_bytes"]
        + sum(blobs.values())
        + snap["neo4j"]["store_bytes"]
    )
    rows.append(row("**Total**", total))
    out.append(table(["store", "size", "per 1,000 docs", "× originals", "notes"], rows))
    graph_rows = [
        [f"label `{k}`", v, f"{v * per:.0f}" if per else ""]
        for k, v in snap["neo4j"]["labels"].items()
    ]
    graph_rows += [
        [f"type `{k}`", v, f"{v * per:.0f}" if per else ""]
        for k, v in snap["neo4j"]["types"].items()
    ]
    out += ["", table(["graph", "count", "per 1,000 docs"], graph_rows)]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_stack_args(ap)
    ap.add_argument("--save", help="write the raw snapshot to this JSON file")
    ap.add_argument("--baseline", help="a saved snapshot to subtract (sizes of this corpus only)")
    ap.add_argument("--label", default="stack total")
    ap.add_argument("--corpus-manifest", action="append", default=[], type=Path)
    args = ap.parse_args(argv)
    c = compose_from_args(args)
    snap = snapshot(c)
    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(json.dumps(snap, indent=1), encoding="utf-8")
    shown = snap
    if args.baseline:
        shown = minus(snap, json.loads(Path(args.baseline).read_text(encoding="utf-8")))
    print(table(["item", "value"], hardware_block(c, args.corpus_manifest)))
    print()
    print(report(shown, args.label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
