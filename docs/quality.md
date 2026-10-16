# Quality numbers

Recorded on the demo corpus (sixteen Project Gutenberg texts, see
`packs/demo/sources.yaml`) with the demo pack v0.1.0, by
`scripts/tag_quality.py` against `packs/demo/golden.yaml`. Document level:
for each of eight texts, which individuals, traditions, text types and
themes should be found, and which must not.

| Date | Pack | Precision (vs absent lists) | Recall | Notes |
|---|---|---|---|---|
| 2026-10-15 | semascope-demo 0.1.0 | 1.00 | 0.98 | 56 of 57 expected tags found; the miss is "epic" in the Iliad, whose text never uses the word. |
| 2026-10-16 | semascope-demo 0.1.0 | 1.00 | 0.98 | the same 56 of 57 on the pinned corpus (`corpus.sha256` `c3122298…`) and the published image `b0e8d27b…`; see the [benchmark run](#benchmark-run-2026-10-16-rc2-cpu-medium). |

How to reproduce on a running stack with the demo loaded (`./oss demo`):

```bash
docker compose run --rm -v "$PWD/scripts:/scripts:ro" -v "$PWD/packs/demo/golden.yaml:/golden.yaml:ro" \
  cli python /scripts/tag_quality.py --collection demo --golden /golden.yaml
```

Span-level precision and recall, and NER quality, arrive with the review
queue in Phase 3, when curators' decisions give a labelled set.

## Performance baseline

Recorded on 2026-10-15 on the development host (AMD Ryzen AI MAX+ 395, CPU
only, `--size medium`, worker concurrency 2) against the development stack's
demo collection, with `scripts/bench.py --runs 10`. Latencies are what the
API measured (Solr time) and end to end from inside the compose network.

The record gave that collection as 22 texts and 27 MB. The development
stack's demo collection held the sixteen demo texts (16.7 MB, the
`demo-corpus` source, hash for hash `packs/demo/corpus.sha256`) plus six
files from a second, `import` source (10.3 MB) added during development,
which together make 22 documents and 27 MB. Those six files are not part of
the repository, so the figures in this section are not tied to a pinned
corpus. The [2026-10-16 run](#benchmark-run-2026-10-16-rc2-cpu-medium) is.

| mode | p50 ms (api) | p95 ms (api) | p95 ms (end to end) |
|---|---|---|---|
| keyword | 41 to 150 | 55 to 185 | 70 to 204 |
| semantic | 17 to 22 | 23 to 46 | 82 to 541 (first query loads the model) |
| hybrid | 42 to 85 | 67 to 133 | 134 to 186 |

Stage time per document (median / p95): structure 54 / 250 ms, patterns
122 / 664 ms, tag 120 / 608 ms, link 9 / 177 ms, preview 2 / 79 ms, index
221 / 632 ms, graph 142 / 430 ms, **ner 28 / 59 s**, **embed 117 / 151 s**.
Named-entity recognition and embedding dominate on book-length texts
(spaCy's small English model over up to 300,000 characters; FastEmbed on up
to 800 chunks on CPU). Ordinary documents of a few pages take well under a
second per stage. Both caps are per-collection settings (`ner_max_chars`,
`embed_max_chunks`).

rc.2 (2026-10-16) cut the text into paragraph windows and dropped the
tagger and parser from the spaCy pipeline. Replaying only the NER stage over
the same 22-document collection (16 of them capped at 300,000 characters; 11 of the 16 pinned demo texts exceed the cap)
on the same host took **5.4 s median, 6.5 s p95, 8.9 s at most** per
document. Part of the difference from rc.1's 28 s is that the rc.1 figure
was measured during full indexing, with embedding running alongside on the
same cores; the windowed pipeline alone measured about a quarter faster
than the whole-text call in isolation.

The `gpu` runtime, measured on an RTX 3090 with two worker processes
replaying the same 22 documents (`reindex --stage ner,embed`): **embed 0.6 s
median, 1.7 s at most** per document (against 117 s median on CPU), and
GLiNER2-large zero-shot entities **15.8 s median, 32 s at most** per
document, slower than spaCy's small model on CPU but asked for the packs'
classes as well as the base four (the Odyssey gained hero, deity and
teacher facets without training). The two processes held 22 GB of the card
at peak. `remote` moves the same stages to services; see
[operations](operations.md#runtimes).

## Benchmark run 2026-10-16: rc.2, CPU, medium

The first run of the [benchmark protocol](assess/benchmarks.md) with pinned
inputs: the published image by digest, both corpora by manifest hash, on a
separate compose project started for the run and removed afterwards.

| item | value |
|---|---|
| CPU | AMD Ryzen AI MAX+ 395 (16 cores, 32 threads) |
| RAM | 121 GiB |
| Disk | Lexar NQ790 4 TB NVMe SSD, ext4 (Docker root) |
| GPUs on the host | RTX 3090 and Radeon 8060S, both unused: `--runtime cpu` |
| OS | Ubuntu 26.04.1 LTS, kernel 7.0.0-38 |
| Docker | Engine 29.9.1, Compose 5.6.0 |
| `oss init` | `--size medium --runtime cpu`: heaps 2 GB each, worker concurrency 2, worker cap 4 GB (the cap before this run's change) |
| Image | `ghcr.io/rdb420/semascope@sha256:b0e8d27b2e59bd8b423f638a9e0acd9b99207db291c5a8a19c639bf3fa7631d4` (`main` as published from `8e4cb2b`, 1.0.0-rc.2 code) |
| Service images | as pinned in `compose.yaml` on 2026-10-16 |
| `demo` corpus | 16 texts, 16.7 MB; `packs/demo/corpus.sha256` sha256 `c3122298b63679ada2d42aeedfe176fcfe1fc77f978e6989117b300924d58c64` |
| `mixed-1k` corpus | 988 files, 35.1 MB; `packs/demo/mixed-1k.sha256` sha256 `9a2794eecc7080afe222446697485dcdfe1910bcd6fcf71c6c7bb9919fa7936f` |

How it was run (the scripts print the hardware block above themselves; the
run also used an overlay that kept Watchtower from updating the containers):

```bash
just mixed-1k        # builds both corpora and fails unless they match the manifests
# a second checkout or worktree with its own .env, so the stack is separate
uv run --package oss-cli oss init --size medium --runtime cpu --https-port 8483 \
  --import-dir "$PWD/packs/demo"
echo "SEMASCOPE_IMAGE_TAG=main@sha256:b0e8d27b2e59bd8b423f638a9e0acd9b99207db291c5a8a19c639bf3fa7631d4" >> .env
docker compose -p sem-bench up -d --wait
B="--project sem-bench --env-file .env"
uv run scripts/bench_size.py $B --save size-empty.json
docker compose -p sem-bench run --rm cli oss demo --corpus /import/corpus
uv run scripts/bench.py $B --collection demo --runs 20 --corpus-manifest packs/demo/corpus.sha256
uv run scripts/bench_size.py $B --baseline size-empty.json --save size-demo.json
docker compose -p sem-bench run --rm cli oss index /import/derived/mixed-1k --collection mixed-1k
uv run scripts/bench.py $B --collection mixed-1k --runs 20 --corpus-manifest packs/demo/mixed-1k.sha256
uv run scripts/bench_size.py $B --baseline size-demo.json
uv run scripts/bench_review.py $B --collection mixed-1k --decisions 400
uv run scripts/bench_recovery.py $B --docs 60
docker compose -p sem-bench down -v
```

### Ingestion throughput

| corpus | documents | originals | wall time | documents per minute | MB per minute |
|---|---|---|---|---|---|
| demo (whole books) | 16 | 16.7 MB | 4.6 min | 3.5 | 3.6 |
| `mixed-1k` (chapters, twelve formats) | 988 | 35.1 MB | 13.6 min | 72.4 | 2.6 |

Wall time runs from the first document's discovery to the last `indexed_at`.
Every document was indexed; none failed.

Stage time per document, median / p95 / maximum in milliseconds:

| stage | demo | `mixed-1k` |
|---|---|---|
| extract | 99 / 194 / 204 | 20 / 4,904 / 8,000 |
| structure | 60 / 201 / 276 | 0 / 2 / 6 |
| patterns | 150 / 378 / 452 | 2 / 7 / 22 |
| tag | 133 / 290 / 375 | 15 / 52 / 146 |
| ner | 6,229 / 12,013 / 12,422 | 261 / 608 / 1,157 |
| link | 2 / 22 / 48 | 0 / 0 / 4 |
| preview | 0 / 0 / 0 | 0 / 629 / 1,293 |
| embed | 32,828 / 40,894 / 47,346 | 559 / 1,220 / 2,203 |
| index | 187 / 245 / 698 | 15 / 54 / 79 |
| graph | 208 / 533 / 2,420 | 19 / 95 / 190 |

On books, embedding (800 chunks at most per document) and NER (300,000
characters at most) take almost all of the time, as in the earlier baseline,
though less of it than the 2026-10-15 record (embed 33 s median against
117 s, NER 6.2 s against 28 s; that record ran rc.1 on the unpinned
collection). In `mixed-1k` the cost is the format: total stage time per
document by media type, in seconds (median / p95):

| media type | documents | seconds |
|---|---|---|
| PDF without a text layer (OCR) | 81 | 6.2 / 8.8 |
| PDF with a text layer | 116 | 1.6 / 2.2 |
| Turtle (read as `text/plain`) | 39 | 1.6 / 2.3 |
| PNG page image (OCR) | 40 | 1.4 / 1.6 |
| ODT, CSV, e-mail, DOCX, HTML, EPUB | 633 | 1.1 to 1.3 / 1.5 to 1.8 |
| zip, mbox | 79 | 0.0 to 0.1 / 0.1 to 0.2 |

OCR took 1.22 s per page (median; 1.87 s p95) over 352 pages. Three of the 81
OCRed PDFs have a text layer; Tika's `AUTO` strategy also OCRs pages with
little text. The 79 zip and mbox files were indexed with no member text (mbox) or
only member names (zip): the filesystem connector keeps a container's own
text, and the archive and mail connectors are what expand them. A folder
mixing them needs those connectors as well.

### Query latency

Five queries, 20 runs each, after indexing finished; filtered runs add two
facet filters (`text-types` = scripture, `themes` = sacrifice). Milliseconds,
API time (`took_ms`) and end to end from the host through Caddy:

| corpus | mode | filtered | p50 api | p95 api | p50 e2e | p95 e2e | max e2e |
|---|---|---|---|---|---|---|---|
| demo | keyword | no | 58 | 100 | 72 | 119 | 141 |
| demo | keyword | yes | 39 | 74 | 52 | 89 | 93 |
| demo | semantic | no | 45 | 57 | 90 | 112 | 397 |
| demo | semantic | yes | 39 | 42 | 80 | 88 | 92 |
| demo | hybrid | no | 50 | 64 | 94 | 115 | 120 |
| demo | hybrid | yes | 40 | 46 | 81 | 90 | 92 |
| `mixed-1k` | keyword | no | 4 | 21 | 17 | 34 | 35 |
| `mixed-1k` | keyword | yes | 2 | 12 | 9 | 23 | 27 |
| `mixed-1k` | semantic | no | 14 | 17 | 66 | 76 | 138 |
| `mixed-1k` | semantic | yes | 1 | 3 | 40 | 47 | 48 |
| `mixed-1k` | hybrid | no | 17 | 19 | 68 | 79 | 113 |
| `mixed-1k` | hybrid | yes | 1 | 5 | 43 | 50 | 53 |

The first query of each mode on the demo, before the runs: keyword 206 ms,
semantic 477 ms (the server loads the query model), hybrid 97 ms end to end.
In semantic and hybrid mode `took_ms` leaves out the query embedding: end to
end is 45 to 50 ms above it, against 13 to 14 ms in keyword mode. The
filtered semantic and hybrid figures are not comparable with the keyword
ones. In rc.2 those modes apply facet filters only when they fetch the page
of results, not to the vector candidates, so `total` ignores the filters and
a page comes back short (on the demo, "flood" counted 12 hits and returned
7). That is a defect this run found; the figures will change when it is
fixed.

### Index and graph size

| store | demo: size | × originals | `mixed-1k`: per 1,000 documents | × originals |
|---|---|---|---|---|
| Solr `documents` | 47.3 MB | 2.84 | 14.8 MB | 0.42 |
| Solr `chunks` | 27.9 MB | 1.67 | 27.4 MB | 0.77 |
| Solr `entities` (the packs' dictionary) | 0.1 MB | 0.01 | 0 | 0 |
| PostgreSQL (database) | 18.6 MB | 1.12 | 23.4 MB | 0.66 |
| Blob store | 16.9 MB | 1.01 | 235.5 MB (originals 35.5, page previews 199) | 6.64 |
| Neo4j store | 0.4 MB | 0.02 | 6.7 MB | 0.19 |
| **total** | 128 MB | 7.7 | 308 MB | 8.7 |

The demo figures are for 16 whole books, so they are given as totals. Graph
per 1,000 `mixed-1k` documents: 1,000 `Document`, 12,575 `Entity` nodes;
40,634 `MENTIONS`, 2,295 `TAGGED`, 1,000 `FROM` and 1,000 `IN` relationships.
The 16 demo books: 6,656 `Entity` nodes, 7,412 `MENTIONS`, 204 `TAGGED`.
Chunks: 9,100 for `mixed-1k` (9.2 per document), 7,019 for the 16 demo
books (at most 800 per document).

### Memory

Container peaks while indexing both corpora (sampled every 5 s): worker 3.84
GiB, Neo4j 2.95, Solr 2.75, Tika 1.08, server 0.70, PostgreSQL 0.08; 11.2
GiB together. The worker's two pool processes grew from about 1 GB to 2.0 to
2.2 GB resident over the run, and the worker reached its 4 GB cap (120 times
at the limit, no process killed). `oss init --size medium` now gives the
worker 6 GB; see [Deployment](assess/deployment.md#memory-and-cpu).

### Reviewer throughput

`mixed-1k` opened 581 review items for 988 documents: 588 per 1,000
documents, 586 of them `link` (an NER entity resembling a pack individual)
and 2 `tag`; no SHACL violations. All 581 were then decided through the API
in two passes, one after another as a single curator would, each link
accepted with its first candidate:

| decision | n | p50 ms | p95 ms | max ms |
|---|---|---|---|---|
| link accept | 579 | 12 | 19 | 108 |
| tag reject | 2 | 16 | 31 | 31 |

A decision is applied to PostgreSQL, Solr and Neo4j before the API answers.
In the second pass, 381 decisions took 5.0 s through the API (4,600 a
minute), and all of them were visible in Solr (`curated_ss`, after its
2-second soft commit) and in Neo4j (299 `CURATED` edges) 8.0 s after the first
one: 2,868 decisions a minute end to end. The rate is the server's; a person
reads far slower.

### Failure and recovery

`scripts/bench_recovery.py`, each scenario on its own collection of 60
`mixed-1k` files (HTML, DOCX, PDF, e-mail, ODT, EPUB and CSV; no OCR),
compared with a clean run of the same files (60 indexed in 39 s: 60 Solr
documents, 690 chunks, 60 graph documents, 2,484 tags). "Lost" is a document
that never reached `indexed`, "half-written" one in PostgreSQL but missing
from Solr or Neo4j, "duplicated" one indexed twice without a reindex.

| scenario | observed | recovered in | lost | duplicated | half-written | result |
|---|---|---|---|---|---|---|
| kill the worker's two pool processes (SIGKILL), 2 documents in flight | one of the two stays `processing`: Celery acknowledges a task whose process died (`WorkerLostError`), so nothing redelivers it; `oss reindex` takes only indexed and failed documents and a source re-run only queued and failed ones, so neither picks it up; still `processing` more than an hour later (the same happened in an earlier, aborted run) | not recovered | 1 | 0 | 0 | **fail** |
| kill the worker container (SIGKILL), 2 in flight | here the tasks stay unacknowledged in Valkey: one document stays `processing` through 10 minutes, a reindex and a source re-run; about 50 minutes after the kill Valkey redelivered both tasks (Kombu's visibility timeout), so the stuck document was indexed and the other, already indexed before the kill, was processed a second time | about 50 min | 0 | 1 | 0 | **fail** |
| restart Valkey with 42 tasks queued | the append-only file kept them; all 60 indexed once | 41 s | 0 | 0 | 0 | pass |
| stop Solr for 30 s during indexing | 42 documents failed (tag, embed and index stages); after `oss reindex` all 60 match the clean run | 51 s after Solr was back | 0 | | 0 | pass |
| stop Neo4j for 30 s during indexing | the graph stage failed on 15 documents, which stayed `indexed` (with the error) rather than `failed`; after `oss reindex` all match | 62 s after Neo4j was back | 0 | | 0 | pass |
| embed stage pointed at a service answering 503 | embed failed on all 60, no chunks and no fallback to the bundled model; documents indexed without vectors; after `reindex --stage embed` with the service back, 690 chunks, all of one model | 40 s | 0 | | 0 | pass |
| `./oss backup --with-graph`, a fresh compose project, `./oss restore` | backup 29 s (510 MB, the whole stack), restore 50 s; 90 checks (hits of three queries in keyword and semantic mode and a facet per collection, review decisions by status, proposals, the last event id and webhook cursors, graph node and relationship counts) all equal | 80 s | | | | pass |
| switch the embedding model (bge-small-en-v1.5 to all-MiniLM-L6-v2) | semantic search returned nothing until re-embedding; `reindex --stage embed` of 60 documents took 13 s; then 10 hits per query again, with 30 to 50 per cent of the old top 10 | 13 s | | | | pass |

Recovery today is `oss reindex`, as the protocol says, and it works for every
failure that leaves a document `failed` or `indexed`. It cannot reach a
document left `processing` by a killed worker. When the pool process dies
(an out-of-memory kill does this), Celery acknowledges the task and the
document is lost until someone requeues it by hand; when the whole worker
dies, Valkey redelivers the task only after the visibility timeout (here
after about 50 minutes), and also redelivers tasks that had finished but
were not yet acknowledged. Two smaller findings: a store outage during the
graph stage leaves documents `indexed` with an error rather than `failed`,
so a status filter does not find them (a reindex of the collection does);
and the run needed a fix to the `./oss` shim, which exited on a host without
`.env` (fixed on this branch).

## Relevance evaluation

Recorded on 2026-10-17 with `oss eval` (see
[Similar documents, exports and annotations](interop.md#relevance-evaluation))
inside a throwaway stack (compose project `sem-extras`, `--size small`,
`--runtime cpu`, embeddings `BAAI/bge-small-en-v1.5`) built from branch
`hardening/extras` at `c9ef5f1` (image `sha256:457ffff5…`), with all sixteen
demo texts loaded (`corpus.sha256` `c3122298…`) and the judgements in
`packs/demo/judgements.yaml` (sha256 `4bebca20…`, 22 queries):

```bash
./oss eval /app/packs/demo/judgements.yaml
```

| mode | P@10 | R@10 | nDCG@10 | MRR@10 |
|---|---|---|---|---|
| keyword | 0.241 | 0.942 | 0.948 | 1.000 |
| semantic | 0.250 | 0.974 | 0.965 | 1.000 |
| hybrid | 0.255 | 0.985 | 0.973 | 1.000 |

How to read it:

- The corpus is sixteen whole books and a query has one to eight relevant
  ones, so P@10 cannot rise much above the share of relevant books; R@10,
  nDCG@10 and MRR@10 carry the signal. Every mode puts a relevant book
  first for every query.
- The judgements come from term counts in the books (`packs/demo/judge.py`),
  so they reward finding the books that talk most about a subject. They say
  nothing about passages, and they are easy for a corpus this small; they
  are a regression baseline, not a claim about search quality in general.
- Hybrid is best overall. Keyword loses most where a query's words differ
  from the translations' (q01 "Odysseus" against Butler's "Ulysses":
  keyword 0.77, semantic 0.95) or are common words (q14 "fables of the
  fox": 0.60, 0.96). Semantic loses on names it embeds poorly (q21 "Pwyll
  and Rhiannon": 1.00 keyword, 0.83 semantic). "Väinämöinen" (q08) is
  found by keyword search too: the analyser folds accents and the Kalevala
  spells the name without them once.

Per query (nDCG@10):

| query | keyword | semantic | hybrid |
|---|---|---|---|
| q01 Odysseus | 0.77 | 0.95 | 0.95 |
| q02 Odysseus returns home to Ithaca and Penelope | 1.00 | 1.00 | 1.00 |
| q03 Achilles and Hector at Troy | 1.00 | 1.00 | 1.00 |
| q04 the great flood | 0.77 | 0.86 | 0.91 |
| q05 Krishna teaches Arjuna on the battlefield | 1.00 | 1.00 | 1.00 |
| q06 the Buddha and Nirvana | 0.97 | 1.00 | 1.00 |
| q07 the forging of the Sampo | 1.00 | 1.00 | 1.00 |
| q08 Väinämöinen | 1.00 | 1.00 | 1.00 |
| q09 King Arthur and his knights | 1.00 | 1.00 | 1.00 |
| q10 Thor, Odin and Loki in Asgard | 1.00 | 0.96 | 1.00 |
| q11 Isis and Osiris | 1.00 | 1.00 | 1.00 |
| q12 the Tao that can be told | 1.00 | 1.00 | 1.00 |
| q13 Confucius on filial piety | 1.00 | 1.00 | 1.00 |
| q14 fables of the fox | 0.60 | 0.96 | 0.87 |
| q15 Hansel and Gretel | 1.00 | 1.00 | 1.00 |
| q16 the priest of Nemi and the golden bough | 0.98 | 0.98 | 1.00 |
| q17 the Trojan war | 0.91 | 1.00 | 1.00 |
| q18 the Prophet and Allah | 1.00 | 1.00 | 1.00 |
| q19 Moses and Pharaoh | 1.00 | 0.99 | 1.00 |
| q20 the gods on Mount Olympus | 1.00 | 0.89 | 1.00 |
| q21 Pwyll and Rhiannon | 1.00 | 0.83 | 0.83 |
| q22 Zeus, king of the gods | 0.85 | 0.81 | 0.84 |

The integration stack loads four of the sixteen texts; there `oss eval`
leaves out the judged books it does not hold and scores the queries that
still have one (`tests/integration/test_extras.py`).
