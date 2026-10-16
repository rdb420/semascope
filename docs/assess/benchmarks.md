# Benchmarks

Status: **draft**, 2026-10-16. The protocol below is what 1.0 will publish.
The figures recorded so far are in [Quality numbers](../quality.md); the
table at the end says which parts of this protocol they already cover.

## Principles

- **Named hardware.** Every result names the CPU, core count, RAM, disk,
  GPU (if any), host OS, Docker version, `oss init --size`, `--runtime` and
  worker concurrency.
- **Pinned inputs.** The corpus is fixed by a manifest of content hashes,
  the pack by its version and hash, and Semascope by its image digest.
- **Scripts in the repository.** Anyone with the same inputs can rerun a
  benchmark with one command and get a Markdown table in the same shape.
- **Cold and warm.** Report the first run after `./oss up` separately from
  steady state.
- **Distributions, not averages.** p50, p95 and maximum for latencies;
  totals and per-document medians for throughput.

## Corpora

| Corpus | Size | Purpose |
|---|---|---|
| `demo` | the sixteen Project Gutenberg texts in `packs/demo/sources.yaml`, with the demo pack | tagging quality against `packs/demo/golden.yaml`; book-length worst case for NER and embedding |
| `mixed-1k` | about 1,000 documents: the demo texts cut into chapters and rendered as PDF, scanned PDF, DOCX, HTML, e-mail and CSV | ingestion throughput across formats, OCR cost |
| `scale-100k` | about 100,000 documents of a few pages each, from a public-domain or openly licensed source | index size, graph size, query latency at the size `--size large` claims |

`mixed-1k` depends on the derived formats ADR-0005 describes
(`packs/demo/derive.py`), which are not yet written. The source of
`scale-100k` is still to be chosen; it must be redistributable or
re-downloadable by a pinned script.

## Measures

| # | Measure | How | Script |
|---|---|---|---|
| 1 | Ingestion throughput | documents and MB per minute from `oss index` to the last `index` stage run; per-stage median and p95 from the documents' stage runs | `scripts/bench.py` (stage part) |
| 2 | Index size | on-disk size of the Solr `documents`, `entities` and `chunks` collections, PostgreSQL and the blob store, per 1,000 documents | to write: `scripts/bench_size.py` |
| 3 | Graph size | node and relationship counts by label and type, and the Neo4j store size, per 1,000 documents | to write, same script |
| 4 | Query latency | five or more fixed queries in each mode (keyword, semantic, hybrid), 20 runs each, with and without two facet filters; API time and end to end | `scripts/bench.py` (search part; facets to add) |
| 5 | Reviewer throughput | open review items per 1,000 documents by kind; decision API latency; decisions per minute applied end to end to Postgres, Solr and Neo4j | to write: `scripts/bench_review.py` |
| 6 | Tagging quality | document-level precision and recall against the golden set; span-level and NER figures once curated decisions provide labels | `scripts/tag_quality.py` |
| 7 | Failure and recovery | the scenarios below; time to recover and whether any document is lost, duplicated or left half-written | to write: `scripts/bench_recovery.py` |

## Failure and recovery scenarios

| Scenario | Expected behaviour in rc.2 | Pass condition |
|---|---|---|
| Kill a worker in the middle of a document | the task is acknowledged late, so Valkey redelivers it to the next worker | every document reaches `indexed` exactly once |
| Restart Valkey during indexing | the append-only file keeps queued tasks | no document is lost |
| Stop Solr or Neo4j during indexing | the stage fails and the document is marked failed; `oss reindex` retries failed documents that have text | after reindex, counts match a clean run |
| A `remote` service answers errors | the stage fails and nothing falls back to the CPU model; reindex recovers | no mixed-model chunks in the index |
| Restore a backup onto a fresh host | `./oss restore` | search, facets, review decisions and the outbox cursor match the source |
| Replace the embedding model | chunks of the old model stop being searched; `oss reindex --stage embed` | semantic search returns to the old recall level |

Automatic retries of failed stages are not in rc.2; recovery today is
`oss reindex`. The recovery benchmark will show how long that takes.

## Reporting

Each run appends a section to [Quality numbers](../quality.md) with the
date, the hardware block, the image digest, the corpus manifest hash, the
command, and the result tables. Results that change by more than 20 per
cent between releases are called out in the changelog.

## What is measured today

| Measure | Status | Recorded |
|---|---|---|
| 1 Ingestion throughput | per-stage times on the demo corpus, CPU and RTX 3090 | 2026-10-15, 2026-10-16 |
| 2 Index size | not measured | |
| 3 Graph size | not measured | |
| 4 Query latency | three modes, no facet filters, demo corpus, CPU | 2026-10-15 |
| 5 Reviewer throughput | not measured | |
| 6 Tagging quality | document level, demo pack 0.1.0: precision 1.00, recall 0.98 | 2026-10-15 |
| 7 Failure and recovery | not measured; covered only by unit tests of the failure paths | |

The performance baseline names its host (AMD Ryzen AI MAX+ 395, CPU only,
`--size medium`, two worker processes) but describes the corpus as 22 texts
and 27 MB, while `packs/demo/sources.yaml` lists sixteen. The next run
should record the corpus manifest hash so the two can be reconciled.
