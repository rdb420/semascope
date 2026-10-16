# Capability matrix

Status: **draft**, 2026-10-16. Describes 1.0.0-rc.2 (commit `8e4cb2b`).

Every advertised capability, with its state and the evidence for it.

| State | Meaning |
|---|---|
| **implemented** | shipped, and covered by automated tests in CI or by the recorded fresh-clone and browser checks |
| **experimental** | code ships, but it is tested only against mocks or not at all, or it has a known defect listed in the notes |
| **planned** | documented or configured, no working code yet |

"Tests" names the evidence: `ci` is the pytest suite run on every push;
`smoke` is the Playwright browser journey (`tests/e2e/ui_smoke.py`, run by
hand against a live stack); `bench` and `quality` are
`scripts/bench.py` and `scripts/tag_quality.py`, whose results are in
[Quality numbers](../quality.md).

## Inputs

| Capability | State | Tests | Notes |
|---|---|---|---|
| Folder (`filesystem`), include and exclude globs, change detection by content hash | implemented | ci | files up to 512 MiB |
| Upload (`oss upload`, `POST /collections/{slug}/upload`) | implemented | ci (connector) | the HTTP endpoint itself has no test |
| Mail: mbox and `.eml`, attachments as child documents | implemented | ci | no PST or MSG |
| Archives: zip and tar (gz, bz2, xz), nested, with size, ratio and depth limits | implemented | ci | 7z and rar are recognised but not unpacked |
| Tables: CSV, TSV and XLSX, one document per row, chosen columns as facets | implemented | ci | |
| RDF files (`.ttl`, `.nt`, `.n3`, `.rdf`, `.owl`, `.jsonld`, `.trig`), one document per subject | implemented | ci | |
| RDF from a SPARQL endpoint | experimental | — | |
| Web pages, sitemaps and bounded same-host crawl, robots.txt respected | experimental | ci (helpers only) | discovery and crawl untested |
| RSS and Atom feeds | experimental | — | |
| S3-compatible buckets | experimental | — | |
| Scheduled sources (cron or `every 15m`) and deletion detection | implemented | ci | |
| Connector plugins (`oss.connectors` entry point) | implemented | ci | |
| Text extraction of any format Tika 3 reads | implemented | smoke | |
| OCR of images and scanned PDFs (Tesseract, English) | implemented | smoke | other languages need a Tika configuration change |
| Language detection | implemented | — | through Tika |
| MinerU extraction (`--extract mineru`) | experimental | ci (mock) | PDF, OOXML Word, PowerPoint and Excel, and raster images go to MinerU by media type (fixed after rc.2, which sent only images); legacy `.doc`, `.ppt`, `.xls` stay with Tika |
| Docling extractor | planned | — | after 1.0 |

## Enrichment

| Capability | State | Tests | Notes |
|---|---|---|---|
| Pages and chunks (`structure`) | implemented | ci | |
| Patterns: emails, URLs, phone numbers, amounts, hashtags | implemented | ci | |
| Dictionary tagging against packs, hierarchical facet per scheme (`tag`) | implemented | quality | precision 1.00, recall 0.98 on the demo golden set |
| Named entities, spaCy on CPU | implemented | bench | English and multilingual small models |
| Named entities, GLiNER2 zero-shot on GPU, pack classes as labels | experimental | ci (parser), bench | measured on an RTX 3090 |
| Named entities, remote `/entities` service | experimental | ci (mock) | |
| Linking entities to pack individuals by label (`link`) | implemented | quality | exact normalised-label match |
| SHACL validation of document metadata (`validate`) | implemented | ci | `full` profile only |
| Previews: PDF pages and image thumbnails | implemented | ci | |
| Chunk embeddings, FastEmbed bge-small on CPU or CUDA | implemented | bench | model baked into the image |
| Embeddings through an OpenAI-compatible endpoint or a sidecar | experimental | ci (sidecar mock, field per dimension) | the `chunks` index has a vector field per dimension (256 to 4096) since the fix after rc.2; `oss doctor` reports a model the schema cannot hold; a change of dimension needs `oss reindex --stage embed` |
| Duplicate detection | implemented (exact) | ci | by content hash; no near-duplicate detection |
| Partial replay (`oss reindex --stage …`) | implemented | ci | |
| Optional LLM answers | planned | — | settings exist; nothing reads them |

## Search

| Capability | State | Tests | Notes |
|---|---|---|---|
| Keyword search, highlighting | implemented | ci, bench | |
| Semantic search over chunks, passages returned | implemented | bench | only chunks of the current model are searched |
| Hybrid search (reciprocal rank fusion) | implemented | bench | |
| Facets, hierarchical facets, multi-select | implemented | ci, smoke | |
| Date histogram and timeline | implemented | ci, smoke | |
| Query tagging (concepts recognised in the query) | implemented | smoke | |
| Saved searches | implemented | ci | |
| Alerts on saved searches | experimental | — | the UI saves with alerts off |
| List search (a batch of names) | experimental | ci (partial) | not in the browser journey |
| Significant terms | implemented | ci, smoke | |
| Suggest / autocomplete | implemented (API) | ci | `GET /api/suggest?q=` (titles, last word as a prefix), routed after rc.2; the search box does not call it yet |

## UI surfaces

| Page | State | Tests |
|---|---|---|
| Sign-in (local accounts; single sign-on button when configured) | implemented | smoke |
| Search: list, media grid, terms, timeline, facets, mode switch, CSV export | implemented | smoke |
| Document: text per page, page previews, tags with decisions, annotations, stage runs | implemented | smoke |
| Packs and pack page: scheme tree, competency questions, deltas | implemented | smoke |
| Concept and individual page with its documents; propose a change | implemented | smoke |
| Graph: hubs, neighbourhood expansion, shortest path | implemented | smoke (hubs, expansion) |
| Review queue | implemented | smoke |
| Map of pack places | implemented | smoke |
| List search | experimental | — |
| Entity page for named entities that are not pack individuals | planned | — |
| Upload, source and collection management in the UI | planned | — |
| Administration (Django admin) | implemented | — |

## Graph operations

| Capability | State | Tests | Notes |
|---|---|---|---|
| Projection of documents, sources, collections, concepts, entities and packs into Neo4j | implemented | ci (keys), smoke | rebuilt by `oss reindex` |
| Pseudonymised entity keys per collection | implemented | ci | |
| Neighbours (depth 1 to 3) | implemented | smoke | |
| Shortest path between two nodes | implemented | — | shown in the UI at the Phase 3 exit check; no automated test |
| Hubs of a collection | implemented | smoke | |
| Read-only Cypher console (staff) | experimental | — | |
| Entity-to-entity relations from pack triples | planned | — | pack relations are in the SPARQL store only |

## Review actions

| Capability | State | Tests | Notes |
|---|---|---|---|
| Review queue: low-confidence tags, link candidates, SHACL violations | implemented | ci | |
| Accept, reject, defer a review item | implemented | ci | |
| Accept, reject, add, remove a tag on a document | implemented | ci | |
| Link an entity to a pack individual | experimental | — | the decision is applied; no test covers it |
| Annotations on documents | implemented | ci | |
| Proposals with stable references, applied at once, kept across reindexing | implemented | ci | append-only log, the newest decision wins, one `external_ref` per decision (fixed after rc.2, where accept, reject, accept was not applied) |
| Curator changes to a pack as deltas | implemented | ci | |
| Merge entities | planned | — | |
| Audit of logins, pack imports, source and collection changes | planned | — | decisions are logged; administrative actions are not |

## Export targets

| Target | State | Tests |
|---|---|---|
| Outbox of decision events, read by cursor | implemented | ci |
| Proposals, read by cursor | implemented | — |
| Webhooks with HMAC signature | experimental | — |
| Pack deltas as `taxonomy.delta.ttl` and `model.delta.yaml` | implemented | ci |
| Search results as CSV (first 10,000) | implemented | — |
| W3C Reconciliation API 0.2 | implemented | ci |
| SPARQL over pack graphs (staff) | experimental | — |
| RDF or JSON-LD export of documents, tags or the graph | planned | — |

## Operation

| Capability | State | Tests | Notes |
|---|---|---|---|
| `oss init`, `oss up`, sizes small, medium and large | implemented | ci (init), fresh-clone check | |
| Runtimes `cpu`, `gpu`, `remote`, a provider per heavy stage | implemented (cpu), experimental (gpu, remote) | ci, bench | |
| Local accounts and API tokens | implemented | ci | |
| Single sign-on (OpenID Connect) | experimental | — | |
| Roles per collection | implemented | ci | roles govern writing; reading is not restricted per collection |
| Backup and restore | implemented | fresh-clone check | no automated test |
| OpenTelemetry traces and metrics | experimental | ci (set-up) | server, workers and beat export both over OTLP since the fix after rc.2; the bundled collector logs traces and passes metrics to Prometheus; not exercised against a live collector in CI |
| Multi-arch images (amd64, arm64) with SBOM and provenance | implemented | release workflow | the `-gpu` image is amd64 only |
| Signed releases | planned | — | provenance attestations, not signatures |
| Helm chart, OpenSearch, multi-instance scaling | planned | — | after 1.0 |
