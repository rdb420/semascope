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
[Quality numbers](../quality.md). [`evidence.yaml`](evidence.yaml) names the
tests behind each row; `scripts/tests/test_claims.py` fails when a row marked
implemented has none, or names a test that does not exist.

## Inputs

| Capability | State | Tests | Notes |
|---|---|---|---|
| Folder (`filesystem`), include and exclude globs, change detection by content hash | implemented | ci | files up to 512 MiB |
| Upload (`oss upload`, `POST /collections/{slug}/upload`) | implemented | ci | |
| Mail: mbox and `.eml`, attachments as child documents | implemented | ci | no PST or MSG; a message's identity is its Message-ID plus a hash of the message; duplicate attachment names are numbered |
| Archives: zip and tar (gz, bz2, xz), nested, with size, ratio and depth limits | implemented | ci | members streamed to disk; one byte budget across every nesting level (100 times the archive's size, at most 4 GiB) and a member count across levels; 7z and rar are recognised but not unpacked |
| Tables: CSV, TSV and XLSX, one document per row, chosen columns as facets | implemented | ci | |
| RDF files (`.ttl`, `.nt`, `.n3`, `.rdf`, `.owl`, `.jsonld`, `.trig`), one document per subject | implemented | ci | |
| RDF from a SPARQL endpoint | implemented | ci | tested against Oxigraph |
| Web pages, sitemaps and bounded same-host crawl, robots.txt respected | implemented | ci | private addresses refused, responses size-capped |
| RSS and Atom feeds | implemented | ci | articles that roll off the feed are kept |
| S3-compatible buckets | implemented | ci | tested against MinIO |
| Scheduled sources (cron or `every 15m`) and deletion detection | implemented | ci | deletes only after a complete run that saw at least half the documents; a failed run deletes nothing; a deleted document comes back when its source has it again (ADR-0013) |
| One run per source at a time | implemented | ci | a second start answers 409; a run that lost its source never deletes |
| Folder path and extension fields from source paths | implemented | ci | `folder_paths` (a tree facet) and `extension_s` |
| Deletion reaches every store: graph, blobs, previews, work files and uploads; a daily sweep removes leftovers | implemented | ci | |
| Connector plugins (`oss.connectors` entry point) | implemented | ci | |
| Text extraction of any format Tika 3 reads | implemented | smoke | |
| OCR of images and scanned PDFs (Tesseract, English) | implemented | ci (images), bench (scanned PDFs) | English by default; per-collection `ocr_languages` (Tesseract codes; the shipped Tika image has deu, eng, fra, ita, jpn, spa); scanned PDFs are measured by the benchmark, not yet asserted in CI |
| Language detection | implemented | ci | through Tika |
| MinerU extraction (`--extract mineru`) | experimental | ci (mock) | PDF, OOXML Word, PowerPoint and Excel, and raster images go to MinerU by media type (fixed after rc.2, which sent only images); legacy `.doc`, `.ppt`, `.xls` stay with Tika; Tika keeps OCR for the formats MinerU does not take; a MinerU provider without an address is a configuration error |
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
| Text of embedded items (attachments, archive members, images in Office files) indexed with their container | experimental | ci (mock) | not separate documents |
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
| Hybrid search (reciprocal rank fusion) | implemented | ci, bench | |
| Facets, hierarchical facets, multi-select | implemented | ci, smoke | |
| Date histogram and timeline | implemented | ci, smoke | |
| Query tagging (concepts recognised in the query) | implemented | ci, smoke | |
| Saved searches | implemented | ci | |
| Alerts on saved searches | implemented | ci, smoke | switched on and off on the Alerts page, with an in-app list of newest matches; outbox events carry no owner, name or hits |
| List search (a batch of names) | implemented | ci, smoke | |
| Significant terms | implemented | ci, smoke | |
| Suggest / autocomplete | implemented (API) | ci | `GET /api/suggest?q=` (titles, last word as a prefix), routed after rc.2; the search box does not call it yet |
| More like this: documents similar to a document (text and passage vectors fused) | implemented | ci, smoke | read-scoped; at most 50 |
| Browser search: OpenSearch description and suggestions | implemented | ci | |
| Relevance evaluation: `oss eval` (P@k, R@k, nDCG@k, MRR@k per mode) with demo judgements | implemented | ci, quality | |
| Original files: authorised download, safe types inline, byte ranges | implemented | ci, smoke | HTML, SVG and unknown types are only sent as attachments |
| Query highlighting on the document page, jump to the first matching page | implemented | ci, smoke | literal matching; a hit found through semantic search may show no highlight |
| Table view: sortable columns and column statistics | implemented | ci, smoke | |
| Entities view (grid of entity facets) | implemented | ci, smoke | |
| Atom and RSS feeds of saved searches, any search and all alerts (feed token) | implemented | ci, smoke | a feed token opens feeds only and reads as its owner |

## UI surfaces

| Page | State | Tests |
|---|---|---|
| Sign-in (local accounts; single sign-on button when configured) | implemented | smoke |
| Search: list, media grid, terms, timeline, facets, mode switch, CSV export, table, entities and graph views, open original | implemented | smoke |
| Document: text per page, page previews, tags with decisions, highlighted occurrences and a "why this tag" panel (provenance and decision history), query highlighting with jump to page, open original and download, annotations, stage runs | implemented | smoke |
| Packs and pack page: scheme tree, competency questions, deltas | implemented | smoke |
| Concept and individual page with its documents; propose a change | implemented | smoke |
| Graph: hubs, neighbourhood expansion, shortest path, edge documents, graph of a search's results | implemented | smoke |
| Alerts and feeds page (saved searches, alerts on and off, feed token, Atom and RSS links) | implemented | smoke |
| Document: similar documents panel and export links | implemented | smoke |
| Overview: counts, top entities and concepts, documents over time | implemented | ci, smoke |
| Review queue | implemented | smoke |
| Map of pack places | implemented | smoke |
| List search | implemented | smoke |
| Entity page for named entities that are not pack individuals | planned | — |
| Upload, source and collection management in the UI | planned | — |
| Administration (Django admin) | implemented | ci |

## Graph operations

| Capability | State | Tests | Notes |
|---|---|---|---|
| Projection of documents, sources, collections, concepts, entities and packs into Neo4j | implemented | ci (keys), smoke | rebuilt by `oss reindex` |
| Pseudonymised entity keys per collection | implemented | ci | |
| Neighbours (depth 1 to 3) | implemented | ci, smoke | |
| Shortest path between two nodes | implemented | ci, smoke | |
| Hubs of a collection | implemented | smoke | |
| Co-occurrence of concepts and entities within a search's results; documents behind an edge | implemented | ci, smoke | |
| Read-only Cypher console (staff) | implemented | ci | write clauses refused; reads run in a read transaction |
| Entity-to-entity relations from pack triples | planned | — | pack relations are in the SPARQL store only |

## Review actions

| Capability | State | Tests | Notes |
|---|---|---|---|
| Review queue: low-confidence tags, link candidates, SHACL violations | implemented | ci | |
| Accept, reject, defer a review item | implemented | ci | every decision, deferrals included, is kept as review history; a decided item answers 409 to a second decision |
| Accept, reject, add, remove a tag on a document | implemented | ci | |
| Link an entity to a pack individual | implemented | ci | the link survives reindexing and records the individual's pack version |
| Annotations on documents | implemented | ci | |
| Import W3C Web Annotations or Hypothesis exports as notes, matched by URI, anchored by quote | implemented | ci | |
| Proposals with stable references, applied at once, kept across reindexing | implemented | ci | append-only log, the newest decision wins, one `external_ref` per decision (fixed after rc.2, where accept, reject, accept was not applied); a decision keeps the tag it was taken on and its pack version; a rejected tag stays on the document, flagged, and is left out of Solr and the graph; the decision, the document's record and the outbox event commit together; Solr and Neo4j are written after it, a periodic sweep applies what a store missed, and hierarchical facets follow the decisions, also after reindex |
| Curator changes to a pack as deltas | implemented | ci |; exported through an RDF serialiser; IRIs and language tags are checked |
| Bulk tagging: accept or reject a concept on every document of a search, as a job, one proposal per document | implemented | ci, smoke | at most 10,000 documents; keyword search and filters; a dry run counts first |
| Explanation of a tag: occurrences with pages, method and stage version, matched label, model, `linked_from`, decision history (API and document page) | implemented | ci, smoke | |
| Merge entities | planned | — | |
| Audit of logins, pack imports, source and collection changes | planned | — | decisions are logged; administrative actions are not |

## Export targets

| Target | State | Tests |
|---|---|---|
| Outbox of decision events, read by cursor | implemented | ci |
| Proposals, read by cursor (`params.tag` carries the decided tag's evidence) | implemented | ci |
| Webhooks with HMAC signature (one delivery run per hook; the cursor never moves back) | implemented | ci |
| Pack deltas as `taxonomy.delta.ttl` and `model.delta.yaml` | implemented | ci |
| Search results as CSV (first 10,000) | implemented | ci |
| W3C Reconciliation API 0.2 | implemented | ci |
| SPARQL over pack graphs (staff) | implemented | ci |
| Documents with their tags in force, notes and decisions as RDF (Turtle, JSON-LD) in the base vocabulary: one document or a search's hits (first 10,000), `oss export` | implemented | ci |
| Notes and decisions as W3C Web Annotations (`GET /api/annotations/w3c`, `oss export annotations`) | implemented | ci |
| RDF of the graph; N-Quads; SPARQL Update push | planned | — |

## Knowledge packs

| Capability | State | Tests | Notes |
|---|---|---|---|
| Pack validation: contract, release manifest, SHACL over the pack's instances, competency questions (`oss pack validate`) | implemented | ci | the ontology skill's own scripts are private; Semascope implements their gates ([ADR-0007](../adr/0007-pack-contract.md)); a `released` pack without its release manifest is an error |
| Pack upgrade as a diff and a reindex plan; activation needs confirmation | implemented | ci | affected documents per collection, counted from Solr |
| Remove a pack version (admin) | implemented | ci | tagged documents keep their tags until reindex |
| Packs in Turtle, N-Triples, RDF/XML or JSON-LD; `skos:notation`; preferred labels in several languages | implemented | ci | shapes must be Turtle |
| Pack precedence for shared IRIs and labels; curator draft labels scoped to their collection | implemented | ci |; a new draft label queues the tag stage for the collection's documents that contain it; the dictionary sync swaps entries and is never empty |
| Pack import and activation as background jobs the UI and `oss pack` follow | implemented | ci | |
| Import a plain list of names as a pack | implemented | ci, smoke | |
| Polyhierarchy (a facet path through each broader concept) and `skos:related` | implemented | ci | |

## Operation

| Capability | State | Tests | Notes |
|---|---|---|---|
| `oss init`, `oss up`, sizes small, medium and large | implemented | ci (init), fresh-clone check | |
| Runtimes `cpu`, `gpu`, `remote`, a provider per heavy stage | implemented (cpu), experimental (gpu, remote) | ci, bench | |
| Local accounts and API tokens | implemented | ci | |
| Single sign-on (OpenID Connect) | implemented | ci | the authorisation-code flow against a test identity provider; rc.2 images lacked the JWT library the callback needs; local sign-up is closed; account creation on first sign-in (`SEMASCOPE_OIDC_AUTO_SIGNUP`) and the new account's role (`SEMASCOPE_OIDC_DEFAULT_ROLE`, default `none`) are configurable |
| Roles per collection | implemented | ci | roles govern reading and writing; reading is per collection by default (`SEMASCOPE_DEFAULT_ROLE=none`; `reader` opens it to every signed-in user); a route-by-route authorisation test covers every endpoint, run with the default |
| Backup and restore | implemented | ci | a round trip onto an emptied stack compares counts, searches, decisions and the outbox cursor; every step fails loudly; restore stages Postgres and Solr beside the live data and switches only when all of it loaded, so a corrupt backup changes nothing |
| Worker loss: no document lost, stuck or processed twice when a worker process or container dies | implemented | ci, bench | a dead process hands its task back; `recover` requeues stale documents every 2 minutes; redelivered work for an unchanged document is skipped |
| Crash-loop guard: a document that kills its worker three times is held as failed | implemented | ci | shown by `oss stats` and `oss doctor`; `oss reindex` retries it |
| Graph projection survives a Neo4j outage | implemented | ci, bench | documents stay searchable with the graph pending, and are projected when Neo4j answers |
| Worker memory: pool processes are replaced past their share of the memory cap | implemented | ci, bench | |
| OpenTelemetry traces and metrics | experimental | ci (set-up) | server, workers and beat export both over OTLP since the fix after rc.2; the bundled collector logs traces and passes metrics to Prometheus; not exercised against a live collector in CI |
| Multi-arch images (amd64, arm64) with SBOM and provenance | implemented | release workflow | the `-gpu` image (amd64 only) has an SBOM too; not yet exercised by a tag |
| Signed releases | experimental | — | keyless cosign signatures in the release workflow, not yet exercised by a tag |
| Release gated on CI and integration (tags only) | experimental | release workflow | the tag must match the version; `ci` and `integration` run on the tagged commit before any image is pushed; not yet exercised |
| Dependency audit and licence policy | implemented | ci | pip-audit and pnpm audit fail the build; GPL and AGPL refused, LGPL only with an ADR (psycopg, ADR-0011); the `-gpu` image's extra wheels are not covered yet |
| README quick start on a fresh clone | experimental | integration (fresh-clone job) | init, up, demo and search pass; `./oss upload` fails until SEMA-019 is fixed |
| Web UI lint, type-check, tests and bundle budget | implemented | ci | first load about 114 kB of gzipped JavaScript |
| Service secrets: none has a default, placeholders refused at start, Valkey requires a password | implemented | ci | |
| Collection settings accept tuning keys only (no service addresses or credentials) | implemented | ci | |
| Outbound requests: one guard for web, feeds, SPARQL, S3 and webhooks (globally routable addresses only, no redirects to private ones, size ceiling) | implemented | ci | a name that changes between the check and the connection is not covered |
| Helm chart, OpenSearch, multi-instance scaling | planned | — | after 1.0 |
