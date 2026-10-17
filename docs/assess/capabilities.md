# Capability matrix

Status: **draft**, updated 2026-10-17. Describes 1.0.0-rc.3 (unreleased: the
main line after 1.0.0-rc.2).

Every advertised capability, with its state and the evidence for it.

| State | Meaning |
|---|---|
| **implemented** | shipped, and covered by automated tests in CI or by the recorded fresh-clone and browser checks |
| **experimental** | code ships, but it is tested only against mocks or not at all, or it has a known defect listed in the notes |
| **planned** | documented or configured, no working code yet |

"Tests" names the evidence: `ci` is the pytest suite, which the `ci`
workflow runs on pushes to `main` and pull requests; `integration` is the
suite against a real stack (`tests/integration`, `just integration` and the
`integration` workflow); `smoke` is the Playwright browser journey
(`tests/e2e/ui_smoke.py`, run by `just integration` and that workflow, or by
hand against a live stack); `bench` and `quality` are
`scripts/bench.py` and `scripts/tag_quality.py`, whose results are in
[Quality numbers](../quality.md). [`evidence.yaml`](evidence.yaml) names the
tests behind each row; `scripts/tests/test_claims.py` fails when a row marked
implemented has none, or names a test that does not exist. The workflows are
in the repository but have not yet run on GitHub for this version: the
evidence is the tests, run locally, not a CI record.

## Inputs

| Capability | State | Tests | Notes |
|---|---|---|---|
| Folder (`filesystem`), include and exclude globs, change detection by content hash | implemented | ci | files up to 512 MiB |
| Sidecar metadata (`.meta.json`, `.xmp`) merged as custom fields | implemented | ci | |
| `oss watch`: folder sources run as soon as files change | implemented | ci | the `watch` compose profile is not run in CI |
| Upload (`oss upload`, `POST /collections/{slug}/upload`) | implemented | ci | |
| Mail: mbox and `.eml`, attachments as child documents | implemented | ci | PST through the `pst` connector (optional extra); `.msg` files are parsed by Tika; a message's identity is its Message-ID plus a hash of the message; duplicate attachment names are numbered |
| Outlook PST/OST (`pst`), messages and attachments | experimental | ci (stand-in for libpff) | needs the optional LGPL `pst` extra, not in the image (ADR-0015) |
| Archives: zip and tar (gz, bz2, xz), nested, with size, ratio and depth limits | implemented | ci | members streamed to disk; one byte budget across every nesting level (100 times the archive's size, at most 4 GiB) and a member count across levels; 7z and rar are recognised but not unpacked |
| Web archives (`warc`): `.warc` and `.warc.gz`, one document per archived page | implemented | ci | original URL, capture date, latest capture wins; `max_record_bytes`, `max_records` |
| Tables: CSV, TSV and XLSX, one document per row, chosen columns as facets | implemented | ci | |
| RDF files (`.ttl`, `.nt`, `.n3`, `.rdf`, `.owl`, `.jsonld`, `.trig`), one document per subject | implemented | ci | |
| RDF from a SPARQL endpoint | implemented | ci | tested against Oxigraph |
| Web pages, sitemaps and bounded same-host crawl, robots.txt respected | implemented | ci | private addresses refused, responses size-capped |
| Scraper rules for web sources (CSS content, title, fields) | implemented | ci | |
| RSS and Atom feeds | implemented | ci | articles that roll off the feed are kept |
| Hypothesis annotations as a source (`hypothesis`) | experimental | ci (mock service) | token masked |
| Mastodon public timelines (`mastodon`) | experimental | ci (mock server) | X/Twitter out of scope |
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
| Patterns: emails, URLs, phone numbers, amounts, hashtags | implemented | ci | plus IBAN, law, currency and regex lists (`oss_pipeline.extractors`) |
| OCR later (`ocr` set to now, later or off): text layer first, OCR on a low-priority pass | implemented | ci | pending documents counted in `/api/stats` and by `oss stats` and `oss doctor` |
| IBANs with check-digit validation (`iban_ss`) | implemented | ci | |
| Legal references (`law_ss`, `LegalReference` entities), configurable patterns | implemented | ci | |
| Amounts with ISO 4217 currency (`currency_ss`, `amount_ss`) | implemented | ci | |
| User regex lists per collection (RE2, `x_<name>_ss`) | implemented | ci | |
| Dictionary tagging against packs, hierarchical facet per scheme (`tag`) | implemented | quality | document-level precision 1.00, recall 0.98 on eight demo texts against the project's own golden set (no span-level figures yet); long texts are paged: windows overlap at a space, a request at the tagger's 5,000 cap is continued, offsets converted from UTF-16 |
| Named entities, spaCy on CPU | implemented | bench | English and multilingual small models; fills the persons, organisations, places and events facets |
| Named entities, GLiNER2 zero-shot on GPU, pack classes as labels | experimental | ci (parser), bench | measured on an RTX 3090 |
| Named entities, remote `/entities` service | experimental | ci (mock) | |
| Linking entities to pack individuals by label (`link`) | implemented | quality | exact normalised-label match; dictionary cached per process and refetched when Solr's fingerprint of it changes |
| Text of embedded items (attachments, archive members, images in Office files) indexed with their container | experimental | ci (mock) | not separate documents |
| SHACL validation of document metadata (`validate`) | implemented | ci | `full` profile only |
| Previews: PDF pages and image thumbnails | implemented | ci | |
| Chunk embeddings, FastEmbed bge-small on CPU or CUDA | implemented | bench | model baked into the image |
| Embeddings through an OpenAI-compatible endpoint or a sidecar | experimental | ci (sidecar mock, field per dimension) | the `chunks` index has a vector field per dimension (256 to 4096) since the fix after rc.2; `oss doctor` reports a model the schema cannot hold; a change of dimension needs `oss reindex --stage embed`; the OpenAI embedder sends 64 inputs per request |
| Duplicate detection | implemented (exact) | ci | by content hash; no near-duplicate detection |
| Partial replay (`oss reindex --stage …`) | implemented | ci | |
| Unknown profile fails the document | implemented | ci | |
| Stages and their clients (Neo4j driver, Solr, services) kept per worker process | implemented | ci | |
| Optional LLM answers | planned | — | settings exist; nothing reads them |

## Search

| Capability | State | Tests | Notes |
|---|---|---|---|
| Keyword search, highlighting | implemented | ci, bench | |
| Per-language analysis: stemmed fields for 28 languages (ISO 639-1), CJK bigrams, routed on the detected language | implemented | ci, quality | Polish needs Solr's analysis-extras module (compose loads it); collections indexed earlier need `oss reindex --stage index` |
| Query syntax: fields, `exact:` / `title:` / `text:`, phrases, AND/OR/NOT, wildcards, fuzzy, ranges; match option and word-form switch | implemented | ci, smoke | |
| Query-time synonyms from the active packs | implemented | ci, quality | individuals' names are equivalent, concepts' labels count at half weight; shared by all collections |
| Sorting; newest first without words | implemented | ci, smoke | |
| Search request limits (page, page size, facet values, deep paging, fused candidates) | implemented | ci | |
| Semantic search over chunks, passages returned | implemented | bench | only chunks of the current model are searched |
| Hybrid search (reciprocal rank fusion) | implemented | ci, bench | |
| Facets, hierarchical facets, multi-select | implemented | ci, smoke | excluded values, show more, a filters-in-force box, and folder-path trees once a connector writes a `*_paths` field |
| Date histogram and timeline | implemented | ci, smoke | click a bar to drill down from year to month to day; date range filter |
| Query tagging (concepts recognised in the query) | implemented | ci, smoke | |
| Saved searches | implemented | ci | |
| Alerts on saved searches | implemented | ci, smoke | switched on and off on the Alerts page, with an in-app list of newest matches; outbox events carry no owner, name or hits |
| List search (a batch of names) | implemented | ci, smoke | phrase, then all words, then near spellings; best score; filters |
| Significant terms | implemented | ci, smoke | scored against the readable collections searched |
| Suggest / autocomplete | implemented | ci, smoke | `GET /api/suggest` (titles, last word as a prefix); the search box suggests titles as you type |
| More like this: documents similar to a document (text and passage vectors fused) | implemented | ci, smoke | read-scoped; at most 50 |
| Ranking explanation per hit: keyword rank, score and clauses (the whole Solr explanation for staff only), nearest passages, fused shares | implemented | ci, smoke | read-scoped |
| Synonym tooling: what a query expands to, candidate labels for a concept, proposals as pack deltas | implemented | ci, smoke | |
| Analysis of a search's results: clusters, corpus comparison, word tree; diff of two documents | implemented | ci, smoke | read-scoped; sampled and capped |
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
| Search: list, media grid, terms, timeline, facets, mode switch, CSV export, table, entities and graph views, open original, title suggestions, sort, match and word-form options, excluded values, date drill-down | implemented | smoke |
| Document: text per page, page previews, tags with decisions, highlighted occurrences and a "why this tag" panel (provenance and decision history), query highlighting with jump to page, open original and download, annotations, stage runs | implemented | smoke |
| Packs and pack page: scheme tree, competency questions, deltas | implemented | smoke |
| Concept and individual page with its documents; propose a change | implemented | smoke |
| Graph: hubs, neighbourhood expansion, shortest path, edge documents, graph of a search's results | implemented | smoke |
| Alerts and feeds page (saved searches, alerts on and off, feed token, Atom and RSS links) | implemented | smoke |
| Document: similar documents panel and export links | implemented | smoke |
| Overview: counts, top entities and concepts, documents over time | implemented | ci, smoke |
| Analysis (clusters, compare, word tree), diff, synonyms, why this result, export and annotation import pages | implemented | smoke |
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
| Curator changes to a pack as deltas | implemented | ci | exported through an RDF serialiser; IRIs and language tags are checked |
| Bulk tagging: accept or reject a concept on every document of a search, as a job, one proposal per document | implemented | ci, smoke | at most 10,000 documents; keyword search and filters; a dry run counts first |
| Explanation of a tag: occurrences with pages, method and stage version, matched label, model, `linked_from`, decision history (API and document page) | implemented | ci, smoke | |
| Merge entities | planned | — | |
| Audit log: sign-ins and sign-outs, users, roles, tokens, collections, sources, webhooks, packs, exports, bulk jobs, console queries | implemented | ci, integration | through the API, the admin or a shell; actor, channel, client address and user agent; append-only (a PostgreSQL trigger refuses updates); details never hold a credential; staff read it at `GET /api/audit` and in the admin; kept `SEMASCOPE_AUDIT_RETENTION_DAYS` (365); not sent to webhooks |

## Export targets

| Target | State | Tests |
|---|---|---|
| Outbox of decision events, read by cursor | implemented | ci |
| Proposals, read by cursor (`params.tag` carries the decided tag's evidence) | implemented | ci |
| Webhooks with HMAC signature (one delivery run per hook; the cursor never moves back) | implemented | ci |
| Pack deltas as `taxonomy.delta.ttl` and `model.delta.yaml` | implemented | ci |
| Search results as CSV (first 10,000) | implemented | ci |
| W3C Reconciliation API 0.2 (signed in unless `SEMASCOPE_RECONCILE_PUBLIC=true`; bounded batches; rate-limited) | implemented | ci |
| SPARQL over pack graphs (staff) | implemented | ci |
| Documents with their tags in force, notes and decisions as RDF (Turtle, JSON-LD) in the base vocabulary: one document or a search's hits (first 10,000), `oss export` | implemented | ci |
| Notes and decisions as W3C Web Annotations (`GET /api/annotations/w3c`, `oss export annotations`) | implemented | ci |
| RDF of a collection's graph (documents, concepts and entities in force, schemes and broader concepts) as Turtle, JSON-LD or N-Quads; N-Quads for every RDF export (`oss export graph`) | implemented | ci |
| SPARQL Update push | planned | — |
| Documents and their concepts and entities as CSV, JSON Lines or Parquet (first 10,000; `oss export documents`, `oss export entities`) | implemented | ci |
| Web Annotation Protocol: create, read, replace and delete one annotation with ETags and If-Match | implemented | ci |

## Knowledge packs

| Capability | State | Tests | Notes |
|---|---|---|---|
| Pack validation: contract, release manifest, SHACL over the pack's instances, competency questions (`oss pack validate`) | implemented | ci | the ontology skill's own scripts are private; Semascope implements their gates ([ADR-0007](../adr/0007-pack-contract.md)); a `released` pack without its release manifest is an error |
| Pack upgrade as a diff and a reindex plan; activation needs confirmation | implemented | ci | affected documents per collection, counted from Solr |
| Remove a pack version (admin) | implemented | ci | tagged documents keep their tags until reindex |
| Packs in Turtle, N-Triples, RDF/XML or JSON-LD; `skos:notation`; preferred labels in several languages | implemented | ci | shapes must be Turtle |
| Pack precedence for shared IRIs and labels; curator draft labels scoped to their collection | implemented | ci | a new draft label queues the tag stage for the collection's documents that contain it; the dictionary sync swaps entries and is never empty |
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
| Roles per collection | implemented | ci | roles govern reading and writing; reading is per collection by default (`SEMASCOPE_DEFAULT_ROLE=none`; `reader` opens it to every signed-in user); a route-by-route authorisation test covers every endpoint, run with the default; packs, concepts, query tags and reconciliation need a role on some collection; reconciliation is anonymous only with `SEMASCOPE_RECONCILE_PUBLIC`; the route test runs as seven kinds of user |
| Backup and restore | implemented | ci | a round trip onto an emptied stack compares counts, searches, decisions and the outbox cursor; every step fails loudly; restore stages Postgres and Solr beside the live data and switches only when all of it loaded, so a corrupt backup changes nothing |
| Worker loss: no document lost, stuck or processed twice when a worker process or container dies | implemented | ci, bench | a dead process hands its task back; `recover` requeues stale documents every 2 minutes; redelivered work for an unchanged document is skipped |
| Crash-loop guard: a document that kills its worker three times is held as failed | implemented | ci | shown by `oss stats` and `oss doctor`; `oss reindex` retries it |
| Graph projection survives a Neo4j outage | implemented | ci, bench | documents stay searchable with the graph pending, and are projected when Neo4j answers |
| Worker memory: pool processes are replaced past their share of the memory cap | implemented | ci, bench | |
| OpenTelemetry traces and metrics | experimental | ci (set-up) | traces to Jaeger (in memory), metrics to Prometheus, a provisioned Grafana dashboard; server, workers and beat export; Flower on loopback behind a password; checked live by hand, not in CI |
| Multi-arch images (amd64, arm64) with SBOM and provenance | experimental | release workflow | the `-gpu` image (amd64 only) has an SBOM too; not yet exercised by a tag |
| Signed releases | experimental | — | keyless cosign signatures in the release workflow, not yet exercised by a tag |
| Release gated on CI and integration (tags only) | experimental | release workflow | the tag must match the version; `ci` and `integration` run on the tagged commit before any image is pushed; not yet exercised |
| Dependency audit and licence policy | implemented | ci | pip-audit and pnpm audit fail the build; GPL and AGPL refused, LGPL only with an ADR (psycopg, ADR-0011); the `-gpu` image's extra wheels are not covered yet |
| README quick start on a fresh clone | implemented | integration (fresh-clone job) | init, up, demo, search and upload of a host file, scripted in the fresh-clone job (`just fresh-clone`); the job has not yet run on GitHub |
| Files on the host for upload, pack import, diff and validate, eval and export | implemented | ci, fresh-clone check | sent into the cli container as a tar on stdin; no bind mounts |
| Secret rotation in the stores and `.env` together (`./oss rotate`) | implemented | ci | PostgreSQL, Neo4j, Solr, admin, API token, Grafana; the pseudonym key is never rotated |
| Secrets from files (`*_FILE`, Docker secrets overlay) | implemented | ci | `compose.secrets.yaml`; the observability profile needs `.env` |
| Health checks on every long-running service; `oss doctor` checks every store, the collections, versions and the base pack | implemented | ci, integration | the collector and Jaeger images have none (no shell); `/healthz` answers 503 without its database |
| Network split: Tika on an internal network, Caddy only with the server | implemented | ci | |
| Sign-in lockout per account and client address; sign-in needs the CSRF token | implemented | ci, integration | every sign-in path (API, admin, allauth); 429 with Retry-After; counted in Valkey; fails open when Valkey is down |
| Per-token rate limits on expensive routes (searches, exports, list search, graph, consoles, jobs, uploads, reconciliation) | implemented | ci, integration | django-ninja throttles; rates in `SEMASCOPE_RATE_LIMITS`; the CLI waits on 429 |
| Request caps: uploaded file size, request body size (server and Caddy), reconciliation batches, query tags, the Cypher console streamed with a timeout | implemented | ci, integration | |
| Credentials masked in API answers, the admin and log lines | implemented | ci | source and collection settings, source errors, webhook addresses, audit details; URL passwords in log lines and tracebacks; the generated CLI token goes to a mode-600 file; access log without query strings |
| Security headers and proxy hardening: page Content-Security-Policy, HSTS, Permissions-Policy, COOP, each once; no server banner; plain HTTP redirected; TLS 1.2+ from `SEMASCOPE_TLS`; optional `/admin/` allow-list | implemented | ci, integration | the policy names the map-tile and sign-on hosts from the settings |
| Error answers without internals (500 with a log reference; store errors logged, not echoed) | implemented | ci | |
| Web UI lint, type-check, tests and bundle budget | implemented | ci | first load about 114 kB of gzipped JavaScript |
| Service secrets: none has a default, placeholders refused at start, Valkey requires a password | implemented | ci | |
| Collection settings accept tuning keys only (no service addresses or credentials) | implemented | ci | |
| Outbound requests: one guard for web pages, robots.txt, sitemaps, feeds, SPARQL, S3 and webhooks (globally routable addresses only, checked address pinned per connection, no redirects to private ones, timeouts, body caps while streaming) | implemented | ci, integration | operator-configured services (Tika, MinerU, embedding and NER providers) use a capped client with timeouts and no address check |
| Helm chart, OpenSearch, multi-instance scaling | planned | — | after 1.0 (OpenSearch here is the search engine as an alternative backend, not the browser description) |
