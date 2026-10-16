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
| Mail: mbox and `.eml`, attachments as child documents | implemented | ci | no PST or MSG |
| Archives: zip and tar (gz, bz2, xz), nested, with size, ratio and depth limits | implemented | ci | 7z and rar are recognised but not unpacked |
| Tables: CSV, TSV and XLSX, one document per row, chosen columns as facets | implemented | ci | |
| RDF files (`.ttl`, `.nt`, `.n3`, `.rdf`, `.owl`, `.jsonld`, `.trig`), one document per subject | implemented | ci | |
| RDF from a SPARQL endpoint | implemented | ci | tested against Oxigraph |
| Web pages, sitemaps and bounded same-host crawl, robots.txt respected | implemented | ci | private addresses refused, responses size-capped |
| RSS and Atom feeds | implemented | ci | |
| S3-compatible buckets | implemented | ci | tested against MinIO |
| Scheduled sources (cron or `every 15m`) and deletion detection | implemented | ci | |
| Connector plugins (`oss.connectors` entry point) | implemented | ci | |
| Text extraction of any format Tika 3 reads | implemented | smoke | |
| OCR of images and scanned PDFs (Tesseract, English) | implemented | ci (images), bench (scanned PDFs) | other languages need a Tika configuration change; scanned PDFs are measured by the benchmark, not yet asserted in CI |
| Language detection | implemented | ci | through Tika |
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
| Hybrid search (reciprocal rank fusion) | implemented | ci, bench | |
| Facets, hierarchical facets, multi-select | implemented | ci, smoke | |
| Date histogram and timeline | implemented | ci, smoke | |
| Query tagging (concepts recognised in the query) | implemented | ci, smoke | |
| Saved searches | implemented | ci | |
| Alerts on saved searches | implemented | ci, smoke | switched on and off on the Alerts page, with an in-app list of newest matches |
| List search (a batch of names) | implemented | ci, smoke | |
| Significant terms | implemented | ci, smoke | |
| Suggest / autocomplete | implemented (API) | ci | `GET /api/suggest?q=` (titles, last word as a prefix), routed after rc.2; the search box does not call it yet |
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
| Proposals with stable references, applied at once, kept across reindexing | implemented | ci | append-only log, the newest decision wins, one `external_ref` per decision (fixed after rc.2, where accept, reject, accept was not applied); a decision keeps the tag it was taken on and its pack version; a rejected tag stays on the document, flagged, and is left out of Solr and the graph |
| Curator changes to a pack as deltas | implemented | ci | |
| Explanation of a tag: occurrences with pages, method and stage version, matched label, model, `linked_from`, decision history (API and document page) | implemented | ci, smoke | |
| Merge entities | planned | — | |
| Audit of logins, pack imports, source and collection changes | planned | — | decisions are logged; administrative actions are not |

## Export targets

| Target | State | Tests |
|---|---|---|
| Outbox of decision events, read by cursor | implemented | ci |
| Proposals, read by cursor (`params.tag` carries the decided tag's evidence) | implemented | ci |
| Webhooks with HMAC signature | implemented | ci |
| Pack deltas as `taxonomy.delta.ttl` and `model.delta.yaml` | implemented | ci |
| Search results as CSV (first 10,000) | implemented | ci |
| W3C Reconciliation API 0.2 | implemented | ci |
| SPARQL over pack graphs (staff) | implemented | ci |
| RDF or JSON-LD export of documents, tags or the graph | planned | — |

## Knowledge packs

| Capability | State | Tests | Notes |
|---|---|---|---|
| Pack validation: contract, release manifest, SHACL over the pack's instances, competency questions (`oss pack validate`) | implemented | ci | the ontology skill's own scripts are private; Semascope implements their gates ([ADR-0007](../adr/0007-pack-contract.md)) |
| Pack upgrade as a diff and a reindex plan; activation needs confirmation | implemented | ci | affected documents per collection, counted from Solr |
| Remove a pack version (admin) | implemented | ci | tagged documents keep their tags until reindex |
| Packs in Turtle, N-Triples, RDF/XML or JSON-LD; `skos:notation`; preferred labels in several languages | implemented | ci | shapes must be Turtle |
| Pack precedence for shared IRIs and labels; curator draft labels scoped to their collection | implemented | ci | |

## Operation

| Capability | State | Tests | Notes |
|---|---|---|---|
| `oss init`, `oss up`, sizes small, medium and large | implemented | ci (init), fresh-clone check | |
| Runtimes `cpu`, `gpu`, `remote`, a provider per heavy stage | implemented (cpu), experimental (gpu, remote) | ci, bench | |
| Local accounts and API tokens | implemented | ci | |
| Single sign-on (OpenID Connect) | implemented | ci | the authorisation-code flow against a test identity provider; rc.2 images lacked the JWT library the callback needs |
| Roles per collection | implemented | ci | roles govern reading and writing; `SEMASCOPE_DEFAULT_ROLE=none` makes reading per collection (the default `reader` keeps it open to every signed-in user); a route-by-route authorisation test covers every endpoint |
| Backup and restore | implemented | ci | a round trip onto an emptied stack compares counts, searches, decisions and the outbox cursor |
| Worker loss: no document lost, stuck or processed twice when a worker process or container dies | implemented | ci, bench | a dead process hands its task back; `recover` requeues stale documents every 2 minutes; redelivered work for an unchanged document is skipped |
| Crash-loop guard: a document that kills its worker three times is held as failed | implemented | ci | shown by `oss stats` and `oss doctor`; `oss reindex` retries it |
| Graph projection survives a Neo4j outage | implemented | ci, bench | documents stay searchable with the graph pending, and are projected when Neo4j answers |
| Worker memory: pool processes are replaced past their share of the memory cap | implemented | ci, bench | |
| OpenTelemetry traces and metrics | experimental | ci (set-up) | server, workers and beat export both over OTLP since the fix after rc.2; the bundled collector logs traces and passes metrics to Prometheus; not exercised against a live collector in CI |
| Multi-arch images (amd64, arm64) with SBOM and provenance | implemented | release workflow | the `-gpu` image is amd64 only |
| Signed releases | experimental | — | keyless cosign signatures configured in the release workflow, not yet exercised by a release |
| Helm chart, OpenSearch, multi-instance scaling | planned | — | after 1.0 |
