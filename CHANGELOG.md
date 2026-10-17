# Changelog

All notable changes are recorded here. The format follows Keep a Changelog
and the project follows semantic versioning.

## [Unreleased]

To be released as 1.0.0-rc.3. The entries are grouped by area; within an
area, new features come first, then changes, then fixes of defects in
1.0.0-rc.2. Everything needed to upgrade an existing install is collected
at the end, under [Upgrade from 1.0.0-rc.2](#upgrade-from-100-rc2).

### Access and sign-in

- Reading is denied by default: a user reads only the collections they
  hold a role on, across search (all modes and exports), documents,
  previews, originals, the graph, curation and stats.
  `SEMASCOPE_DEFAULT_ROLE` defaults to `none` and `oss init` writes it; set
  `reader` to open reading to every signed-in user.
- Every API route has an explicit role rule, checked by a test that walks
  each route as seven kinds of user; annotations need the curator role (or
  authorship), document reindex the editor role, and marking pack deltas
  exported is a POST for editors.
- Packs, concepts and reconciliation need a role on at least one
  collection; the Reconciliation API is anonymous only with
  `SEMASCOPE_RECONCILE_PUBLIC=true` (OpenRefine needs it).
- Local sign-up is closed; single sign-on creates accounts only while
  `SEMASCOPE_OIDC_AUTO_SIGNUP` is true and gives them
  `SEMASCOPE_OIDC_DEFAULT_ROLE` (default `none`).
- Sign-in needs the CSRF token, and repeated failures lock the account
  (`SEMASCOPE_LOGIN_MAX_FAILURES`, 5) and the client address (50) out for
  `SEMASCOPE_LOGIN_LOCKOUT_S` (15 minutes) on every sign-in path; the API
  answers 429 with Retry-After. Scripts that sign in through
  `POST /api/auth/login` must send the CSRF token (bearer tokens are
  unaffected).
- An audit log records sign-ins (including failures and lockout refusals),
  sign-outs, changes to users, roles, API and feed tokens, collections,
  sources, webhooks and pack versions (through the API, the Django admin or a
  shell), pack imports and activations, exports, bulk tagging, reindexing,
  uploads, annotation imports and deletions, and console queries, with the
  actor, channel, client address and user agent. Entries are append-only, hold
  no credentials, are kept for `SEMASCOPE_AUDIT_RETENTION_DAYS` (365) and are
  read by staff at `GET /api/audit` or in the admin.
- API tokens record their last use at most every five minutes.
- Fixed: single sign-on through OpenID Connect failed at the callback: the
  image lacked PyJWT and cryptography (`django-allauth` now with its
  `socialaccount` extra).

### Secrets, network and request limits

- The server, workers and beat refuse to start with an empty or placeholder
  secret outside debug mode; no secret has a default value; Valkey requires
  `VALKEY_PASSWORD` (`./oss up` adds one to older `.env` files) and Grafana
  `GRAFANA_PASSWORD`.
- Any setting can be read from a file named by `<NAME>_FILE`; `oss init
  --secrets-dir` and `compose.secrets.yaml` keep every secret out of `.env`.
  `./oss rotate` changes secrets in PostgreSQL, Neo4j, Solr, the admin
  account, the API token and Grafana together with `.env`.
  `SEMASCOPE_PSEUDONYM_KEY` keys graph pseudonyms apart from the secret key
  and is never rotated.
- Solr requires basic authentication, configured from `SOLR_PASSWORD` at
  start, including on existing installs; backup, restore and `oss doctor`
  send the credentials.
- Tika runs on an internal network shared only with the worker and the cli;
  Caddy reaches only the server.
- Credentials are masked wherever they could be shown: source settings
  (withheld entirely below editor; account identifiers masked below editor),
  collection settings, source errors, webhook addresses, the admin forms
  (which keep stored secrets), audit details and log lines (URL passwords,
  tracebacks included). Source credentials stay in the source settings. The
  CLI token generated at first start goes to `/data/cli-token` instead of the
  log, and the access log leaves out query strings. Readers and curators no
  longer see source settings.
- A collection's `settings.pipeline` accepts tuning keys only; service
  addresses and credentials come from the deployment.
- Outbound requests to any URL a source, user or document supplies resolve
  the host once per connection, refuse it unless every address is globally
  routable or allowed by `SEMASCOPE_ALLOWED_PRIVATE_HOSTS`, and connect to the
  checked address while TLS verifies the host name, so DNS rebinding cannot
  reach a private address. Redirects stop after 10, environment proxies are
  not used for them, every client has timeouts, and bodies are capped while
  streaming (S3 objects by the bytes actually read); `max_bytes` has a 1 GiB
  ceiling. File sources read only under `SEMASCOPE_SOURCE_ROOTS`; web, feed,
  SPARQL and S3 sources share one private-address guard and response size
  caps. Webhooks use the guard too and never follow redirects. Tika, MinerU
  and the embedding and NER providers get timeouts, no redirects and a body
  cap; `oss export` notices a stalled server.
- Custom `x_` fields from sources cannot replace the index's own fields; RDF
  sources refuse JSON-LD with remote contexts. Search field names are
  validated, every value in a Solr query is quoted, and collection slugs
  must be slugs, including where they name folders.
- The expensive routes (searches, exports, list search, graph, the Cypher
  and SPARQL consoles, queued jobs, uploads, reconciliation) are rate-limited
  per token, with the rates in `SEMASCOPE_RATE_LIMITS`; the CLI waits and
  retries. Counters live in Valkey (its own database). Uploaded files are
  capped by `SEMASCOPE_UPLOAD_MAX_MB` and other request bodies by
  `SEMASCOPE_REQUEST_MAX_MB`; reconciliation batches, query tags and the
  Cypher console (now streamed in a read transaction with a timeout) are
  bounded.
- The server's pages carry a Content-Security-Policy; Caddy sets HSTS,
  Permissions-Policy and the other headers once each, removes the Server
  header, redirects plain HTTP on the HTTPS port, caps request bodies, takes
  its certificate from `SEMASCOPE_TLS` and can limit `/admin/` to
  `SEMASCOPE_ADMIN_ALLOW`.
- Unexpected errors answer with a log reference instead of a traceback;
  store errors are logged, not echoed. `/healthz` answers 503 without its
  database.
- Fixed: `CSRF_TRUSTED_ORIGINS` follows `SEMASCOPE_HTTPS_PORT` (new
  setting, default 8443) instead of assuming 443 and 8443.

### Sources and ingestion

- New connectors: `warc` (web archives), `hypothesis` (annotations),
  `mastodon` (public timelines) and `pst` (Outlook PST/OST through the
  optional LGPL `pst` extra, ADR-0015). Scraper rules for web sources;
  sidecar metadata files (`.meta.json`, `.xmp`); `folder_paths` and
  `extension_s` fields from source paths; `oss watch` and a `watch` compose
  profile.
- OCR later (`settings.pipeline.ocr: now|later|off`): documents are indexed
  from their text layer at once and scans are OCRed afterwards on a
  low-priority task (ADR-0014). Per-collection `ocr_languages`.
- The text of embedded items (attachments, archive members, images in
  Office files) is indexed with their container.
- Deletion and retention (ADR-0013). Fixed: source runs no longer lose
  documents: a run deletes what its source no longer has only when it read
  everything it tried, did not stop at a limit and saw at least half the
  documents; a site, feed or sitemap that cannot be read fails the run;
  feeds keep articles that roll off; a deleted document comes back when its
  source has it again; one run per source at a time (409 while running).
- Deleting a document, source or collection also removes its graph nodes,
  blobs, previews, work files and uploads; a daily `gc` sweeps leftovers.
- Fixed: the archive connector streams members to disk within one byte
  budget across every nesting level, as documented. Mail URIs include a hash
  of the message and duplicate attachment names are numbered. Blobs are
  named by the hash of what was read. Titles with several values and
  regional language codes are read correctly.
- Fixed: the `pst` extra installed an unrelated package and pulled in
  astropy, dask and zarr.
- Fixed: MinerU receives PDF, OOXML Word, PowerPoint and Excel, and raster
  image files, chosen by media type (rc.2 sent only images); Tika keeps OCR
  for the formats MinerU does not take.

### Pipeline and enrichment

- Pattern extractors for IBANs, legal references, amounts with currency and
  per-collection RE2 regex lists.
- Every tag explains itself: the stage version, the dictionary label and
  label type that matched (and its language), the NER model, and the named
  entity that linking replaced (`linked_from`); each occurrence records its
  page and the label it matched (ADR-0009).
- One task per document on two queues: `oss-worker --queues` defaults to
  `io,cpu` and stages no longer declare a queue (ADR-0010). Stopping the
  worker hands unfinished tasks back after 45 s.
- Worker loss no longer loses or strands documents: a dead pool process
  hands its task back, `recover` (every 2 minutes) requeues documents left
  `processing`, and a redelivered task for a document already indexed from
  the same content and settings does nothing. A document whose runs kill
  the worker three times (`SEMASCOPE_MAX_ATTEMPTS`) is held as failed until
  it changes or `oss reindex`. Document tasks have a hard time limit
  (`SEMASCOPE_TASK_TIME_LIMIT_S`, 1800 s, 5400 s with MinerU) and Valkey's
  visibility timeout follows it.
- Worker pool processes are replaced once they grow past their share of the
  memory cap (`SEMASCOPE_WORKER_MAX_MEMORY_MB` overrides it).
- A Neo4j outage leaves documents searchable with `graph_status: pending`;
  the projection is written once Neo4j answers (migration `catalogue.0004`).
- Stages and their clients live as long as the worker process (the graph
  stage keeps one Neo4j driver for the process); the OpenAI-compatible
  embedder sends 64 inputs per request (`SEMASCOPE_EMBED_BATCH`).
- Fixed: transient failures of remote providers, Solr, Neo4j, Tika and
  MinerU are retried: twice in place (2 s, 4 s), then the document task up
  to three times (60, 120, 240 s); permanent failures are not retried and
  nothing falls back to a CPU model. A failed fetch no longer leaves a
  document in `processing`.
- Fixed: tagging no longer drops matches: a request at the tagger's 5,000-
  match cap is continued, window edges overlap at a space, and UTF-16
  offsets are converted, so spans after an emoji stay on their words. NER
  events reach the `events_ss` facet. A skipped embed stage deletes the
  document's old chunks. The validate stage survives carriage returns and
  odd IRIs. The link stage picks up re-synced dictionaries and draft labels
  without a restart. An unknown profile fails its documents instead of
  running `default`; a remote provider without a base URL fails the stage.
- Fixed: NER labels and SHACL shapes come from the collection's bound packs.
- Fixed: a dictionary tag's confidence is the best label type over all its
  occurrences, not that of its first one.
- Fixed: the `chunks` index has a vector field per dimension (256, 384, 512,
  768, 1024, 1536, 2560, 3072, 4096), so embedding models other than 384
  dimensions work; `oss doctor` reports a model the schema cannot hold (new
  optional `SEMASCOPE_EMBED_DIMENSIONS`).
- Fixed: the worker initialises OpenTelemetry in every pool process, and
  server, worker and beat export metrics as well as traces
  (`semascope.documents`, `semascope.stage.duration`, plus the
  instrumentation's own).

### Search

- Per-language search for 28 languages (ar bg ca cs da de el en es fa fi fr
  hi hu id it ja ko lv nl no pl pt ro ru sv tr zh): each document is also
  indexed with its language's stop words and stemmer, Chinese, Japanese and
  Korean as CJK bigrams. Solr loads the analysis-extras module.
- Analysis of a search's results (ADR-0012): clusters of documents by their
  words or meaning, comparison of a search's words with another search or
  the whole collection, a word tree around a phrase, and a line and word
  diff of two documents. Synonym tooling shows what a query expands to and
  suggests labels curators can propose. "Why?" on each result explains its
  rank.
- Query-time synonyms from the active packs.
- A documented search syntax (`docs/search.md`) with `exact:`, `title:` and
  `text:`, a match option and a word-form switch.
- The search page suggests titles, sorts (newest first without words),
  leaves facet values out, shows more values, lists the filters in force,
  drills down the timeline, shows folder-path trees and entity chips. List
  search tries the phrase, then all words, then near spellings.
- Significant terms are scored against the readable collections searched.
- Search parameters are bounded (page_size 0–100, page 1–500, facet_limit
  1–100, no paging past the 10,000th hit); hybrid search fuses the whole
  keyword list.
- Fixed: semantic and hybrid search apply every filter to the vector
  candidates, so totals and pages are right when facets are selected;
  deletion removes a document's passages too.
- Fixed: an explicit OR no longer behaves as AND. English stop words and
  stemming are no longer applied to every language. A numeric query such as
  `1984` survives the search page; the media view counts its pages; a failed
  CSV export is reported; saved searches keep their mode, view and options.
- Fixed: `GET /api/suggest` is routed (rc.2 listed it but never routed it).

### Documents, views and the web UI

- Original files: `GET /api/documents/{id}/original` serves the stored
  original to readers of its collection, with its file name, byte ranges
  and an ETag; PDFs, images, plain text, audio and video open in the
  browser, HTML, SVG and other types download (ADR-0016).
- The document page highlights the words of the search that led to it and
  jumps to the first matching page (`GET /api/documents/{id}/matches`).
- Table, entities and graph-of-results views of a search; clicking a graph
  edge lists the documents behind it.
- Alerts page, and Atom and RSS feeds of a saved search, any search and all
  alerting searches, read with a revocable feed token. Feed tokens open
  feeds only and read as their owner.
- `GET /api/curation/documents/{id}/tags` returns occurrences and
  provenance, and `…/tags/history?iri=` every decision on a tag. The
  document page highlights a tag's occurrences and shows a "why this tag"
  panel.
- An Overview page.
- Originals are read only from the blob store, and active content is sent
  as an attachment with a sandbox content-security policy; curator controls
  show only to curators.
- The web UI loads pages and the map, graph and chart libraries on demand:
  about 114 kB of gzipped JavaScript on a first visit instead of 697 kB.

### Curation and knowledge packs

- Bulk tagging: a curator accepts or rejects a concept on every document of
  a keyword search in a collection (at most 10,000), as a job with one
  proposal per document (`POST /api/curation/bulk-tags`, with a dry run).
- Every decision on a review item, deferrals included, is kept
  (`ReviewDecision`, migration `curation.0003`); a decision keeps the tag it
  was taken on and its pack version; graph edges carry `stage_version` and
  `pack_version`. A decided review answers 409 to a second decision.
- A rejected tag stays on the document, flagged, instead of being removed;
  search and the graph leave it out through the same curated view.
- Curation decisions commit to PostgreSQL and the outbox first; Solr and
  Neo4j are written after the commit, and `retry-proposals` (every 5
  minutes) applies what a store missed.
- Alert events in the outbox carry no owner, search name or hits; webhook
  deliveries and alert checks take a lease, so no event is sent twice.
- Pack imports and activations run as jobs that the pages and `oss pack`
  follow (`/api/knowledge/pack-jobs`); import and activate answer 202 with a
  job, after the same plan check and 409. A plain list of names imports as a
  pack (`oss pack import-list`); polyhierarchy gives a concept a facet path
  through each broader concept, and `skos:related` is kept and shown.
- Packs: `oss pack validate` and import verify `release-manifest.json`
  (a mismatch is an error unless `--force`; a `released` pack without
  `release-manifest.json` is an error), run SHACL over the pack's own
  instances, check the query manifest, and fail on concepts without a
  preferred label, duplicate ids and unknown evidence ids; non-semver
  versions and `owl:imports` warn. Competency questions run over the dataset
  each manifest entry names, and an unknown expectation type fails.
- Pack upgrades are a diff and a reindex plan (`oss pack diff`,
  `POST /api/knowledge/packs/diff`, `GET /api/knowledge/packs/{id}/diff`);
  replacing an active version needs `--yes` / `confirm=true`; new
  `oss pack activate`. A non-active version can be removed
  (`oss pack remove`, `DELETE /api/knowledge/packs/{id}/versions/{version}`,
  admin).
- Packs may use N-Triples, RDF/XML or JSON-LD; `skos:notation` and preferred
  labels in further languages are kept.
- The dictionary sync adds new entries before removing old ones.
- Fixed: curator draft labels tag only the collection that proposed them and
  survive dictionary syncs (new `collection_scope` field in the `entities`
  schema).
- Fixed: pack conflicts resolve by binding precedence: an IRI in two active
  packs no longer breaks `GET /api/knowledge/concepts?iri=` (which takes an
  optional `collection`), and the tag and link stages choose the
  higher-precedence pack instead of Solr's order.
- Fixed: a rejected concept stayed in the hierarchical facet, even after a
  reindex, and an added one never appeared there. Delta export could be made
  to emit extra triples. The pack triple store was loaded inside the import
  transaction.
- Fixed: a rejected tag came back in PostgreSQL after a reindex, and a
  curator's link to a pack individual was undone by one.
- Fixed: the proposal log is append-only and the newest decision on a target
  wins, so accept, reject, accept leaves the tag accepted (rc.2 returned the
  first proposal unapplied). Each decision has its own `external_ref`,
  chained to the one it supersedes (`supersedes` in the outbox event);
  repeating the decision in force is a no-op that re-applies it if a store
  missed it. Migration `curation.0002` also drops the never-produced kinds
  (review `delta`; proposals `add_label`, `new_concept`, `merge_entities`).
- Fixed: `CURATED` edges to named entities use the graph stage's key
  (`<Class>/<label>`, or its HMAC when the collection pseudonymises), so they
  land on the node the machine linked.
- Fixed: pack import, SPARQL and version removal failed in all but one
  server process (Oxigraph's lock) and could hang after the server forked;
  each pack-store operation now runs in its own process. SPARQL ASK over a
  pack returned no rows. Re-importing an unchanged pack with activation left
  it inactive. The pack SPARQL console refused a SELECT or ASK that started
  with PREFIX declarations.

### Exports and interoperability

- More like this (`GET /api/documents/{id}/similar`), RDF exports of
  documents and searches in Turtle and JSON-LD (`oss export`), W3C Web
  Annotation export and import (including Hypothesis exports), an OpenSearch
  description for browsers, and `oss eval` relevance scores with demo
  judgements (ADR-0012).
- Exports as tables: documents and their concepts and entities as CSV, JSON
  Lines or Parquet (`oss export documents|entities`), with recipes for
  DuckDB, Superset and Grafana; RDF exports also write N-Quads, and a
  collection's graph exports as RDF (`oss export graph`). The Web Annotation
  Protocol's per-annotation create, read, replace and delete with ETags.
  These exports are rate-limited and recorded in the audit log.

### Operations

- `./oss upload`, `pack import|diff|validate`, `eval` and `export --out`
  work with files on the host; `./oss init` works without uv. Fixed:
  `./oss upload` of a host file; the shim creates the import folder before
  Docker can create it as root.
- `oss doctor` checks PostgreSQL, Valkey, the Solr collections and SolrCloud
  mode, versions and the base pack. `oss stats` and `oss doctor` report
  documents whose graph projection is pending or failed, documents held
  after repeated worker crashes, and documents waiting for OCR.
- Health checks for the worker, beat and Caddy. Fixed: the server no longer
  runs healthy without its Solr collections. The server runs its start-up
  preparation in a child process, so the web server never holds the pack
  store open across a fork.
- The observability profile has traces in Jaeger, a Grafana dashboard and
  Flower on loopback behind a password. `.env.example` documents the
  settings.
- `oss init --size` sets the worker's processes and memory cap;
  `SEMASCOPE_MAP_TILE_URL` (empty for no base map) for offline installs.
  `oss init --force` keeps every non-secret setting.
- `oss demo --only`, `SEMASCOPE_ENV_FILE` for a second stack beside the
  first.
- `compose.yaml` defaults the image tag to the checkout's version instead of
  `main`; `:main` is no longer published. Service images in the compose
  files are pinned by digest (`just bump-images`). Build bakes fail the
  build.
- Fixed: backup and restore fail loudly at any failed step; restore checks
  the backup and loads it beside the live data before switching, so a
  corrupt backup changes nothing. Backup and restore keep every Solr
  configset file: configsets have no subfolders, because a Solr backup drops
  them. Restore puts the Solr configsets back from the image after any
  RESTORE. `DATABASE_URL` passwords are URL-decoded.
- Importing the settings no longer creates `data/static`.
- Routes return django-ninja `Status` objects; its deprecation warnings fail
  the tests.
- Fixed: `/healthz` no longer reports a stale `phase`; stale phase notes
  removed from docstrings, `deploy/neo4j/README.md` and the plan; the
  concepts page lists the `s3` and `rdf` connectors.

### Tests, CI and releases

- Integration tests against the real stack (`tests/integration`,
  `just integration`), including real web, feed, S3 (MinIO), SPARQL
  (Oxigraph), webhook and OpenID Connect services, the browser journey and a
  backup and restore round trip; the `integration` workflow runs them on
  amd64, and weekly on arm64. `docs/assess/evidence.yaml` maps every
  capability-matrix row to the tests that prove it, and a test keeps the two
  in step.
- CI jobs for the web UI (lint, type-check, tests, bundle budget), the
  supply chain, workflow linting and the README quick start on a fresh
  clone; a pull request template with the clean-room confirmation. The
  clean-room check compares against the pinned legacy sources, and
  `cleanroom_check.py` fails when no legacy tree is given.
- Every GitHub action is pinned by commit SHA with least-privilege
  permissions; `pip-audit` and `pnpm audit` fail the build; a licence gate
  refuses GPL and AGPL dependencies, and LGPL ones unless an ADR accepts them
  (psycopg, ADR-0011). CI validates `packs/base` and `packs/demo/pack`
  without rewriting their manifests; the base pack's stale manifest is
  regenerated.
- The release workflow publishes only from `v*` tags, after `ci` and
  `integration` pass on the tagged commit, and signs the images with keyless
  cosign; the `-gpu` image gets an SBOM too. The docs deploy no longer tries
  to enable Pages itself.
- The demo corpus is pinned by hash; `packs/demo/derive.py` builds the
  `mixed-1k` corpus in twelve formats; benchmark scripts for size, review
  throughput and failure recovery, and a hardware block in `bench.py`.
- Fixed: `scripts/integration.sh` creates the import folder before its first
  container run, so a fresh checkout no longer gets a root-owned `data/`.

None of the workflows above has run on GitHub for these changes yet, and no
`v*` tag exists: the release workflow, its signatures and attestations and
the Pages deploy are configured, not yet exercised.

### Documentation

- New pages: install guide, connector settings, languages and OCR, an API
  overview, events and webhooks, writing plugins, and product decisions;
  ADR-0016 (original files); ADR-0006 amended to the stack as built.

### Upgrade from 1.0.0-rc.2

Back up first (`./oss backup`), then:

1. **Start with `./oss up`, not plain `docker compose up`,** once: it adds
   a generated `VALKEY_PASSWORD` to `.env`, uploads the new `entities`,
   `chunks` and `documents` configsets, runs the migrations (`audit.0001`,
   `catalogue.0004`, `curation.0002`, `curation.0003` among them) and
   recreates the containers on their new networks with their health checks.
   The application refuses to start with an empty or placeholder secret; the
   observability profile needs `GRAFANA_PASSWORD`.
2. **Decide who reads what.** Reading is now per collection by default: set
   `SEMASCOPE_DEFAULT_ROLE=reader` in `.env` to keep open reading, or give
   users roles.
3. **Scripts and integrations.** Scripts that sign in with a password need
   the CSRF token (`GET /api/auth/csrf`, then `X-CSRFToken`); OpenRefine
   needs `SEMASCOPE_RECONCILE_PUBLIC=true`. Clients of the pack import and
   activate API must follow the returned job. Alert webhook payloads are now
   `{saved_search, new, total}`. The expensive routes are rate-limited.
4. **Reindex each collection** (`./oss reindex --collection <slug>`), for:
   corrected spans and the events facet (stage versions changed: tag 3,
   index 3, validate 2); the per-language fields (`--stage index` is
   enough for those); and tree facets built from the curated view.
   Synonyms need no reindex.
5. **Packs.** Re-activate packs to fill multi-parent paths. To move
   existing draft labels to their collection, re-activate each pack that
   has `add_label` deltas
   (`POST /api/knowledge/packs/{id}/activate?version=`), which re-syncs its
   dictionary and queues a reindex of the collections that use it; until
   then, drafts written by rc.2 still tag every collection.
6. **Embeddings.** Existing 384-dimension chunks stay in the `vector` field
   and need no reindex; after switching to a model of another dimension, run
   `./oss reindex --collection <slug> --stage embed` for each collection.
7. **Pseudonyms.** Without `SEMASCOPE_PSEUDONYM_KEY` in `.env`, the secret
   key keys the graph pseudonyms as before, so they stay the same; `oss init
   --force` and rotating `SEMASCOPE_SECRET_KEY` with `./oss rotate` write
   the old secret key into `SEMASCOPE_PSEUDONYM_KEY`.
8. **Mail.** Existing mail documents are re-created once under their new
   URIs.
9. **Disk and limits.** Restore needs free disk for a second copy of the
   Solr collections and the database while it runs. Sources with
   `max_bytes` above 1 GiB are clamped. Caddy takes `SEMASCOPE_TLS`,
   `SEMASCOPE_ADMIN_ALLOW` and `SEMASCOPE_UPLOAD_LIMIT` from `.env`; a
   Caddyfile edited for a certificate can go back to the shipped one with
   `SEMASCOPE_TLS` set instead.

## [1.0.0-rc.2] - 2026-10-16

### Added

- Runtimes (ADR-0008): `oss init --runtime cpu|gpu|remote` with a provider
  per heavy stage (`--embed`, `--ner`, `--extract`). `gpu` runs the worker
  from a new `-gpu` image (`deploy/docker/Dockerfile.gpu`: FastEmbed on
  ONNX Runtime CUDA, GLiNER2 zero-shot entities) that the release workflow
  publishes for amd64. `remote` calls HTTP services: an embedding sidecar
  (`/embed/dense`) or OpenAI `/embeddings`, a GLiNER-shaped `/entities`
  service, and a MinerU parse server for PDF, Office and images.
- Zero-shot entity providers are asked for every class of the active packs,
  with the class definition as the description; pack-class entities get
  their own facet.
- Chunks are searched only for the current embedding model;
  `oss reindex --stage embed` (or any stage list) replays part of the
  profile, keeping the other stages' tags and publishing nothing when a
  replayed stage fails; a reindex also retries failed documents that have
  text; `oss stats` reports chunk counts per model; `oss doctor` checks
  the configured remote services.

### Changed

- The NER stage cuts text into paragraph-bounded windows and runs spaCy's
  pipeline with only tok2vec and ner (about a quarter faster on book-length
  texts); the stage version is 2.
- `compose.gpu.yaml` now builds from a real Dockerfile (rc.1 referenced one
  that did not exist) and `./oss up` adds it when the runtime is `gpu`.

## [1.0.0-rc.1] - 2026-10-15

First release candidate: every phase of the development plan is
implemented and the fresh-clone exit test passes (init, up, index, search in
three modes, packs, UI, backup). Images are published by the release
workflow once the repository is on GitHub.

### Added

- Phase 3 graph and exploration: Neo4j projection with CURATED edges and
  optional pseudonymisation; graph API and view with hubs, neighbourhoods
  and shortest paths; review queue, proposals with stable refs, annotations,
  event outbox and webhooks; saved searches with alerts, list search, CSV
  export, result terms, timeline, media grid, map of pack places; S3 and
  RDF connectors
- Phase 4 so far: chunk embeddings (FastEmbed, model baked into the image,
  or any OpenAI-compatible endpoint), semantic and hybrid search, roles per
  collection, optional OpenID Connect, backup and restore

- Phase 2 knowledge model: pack registry and importer on an embedded
  Oxigraph store with competency tests at import; Solr `entities`
  dictionary and Tagger; tag, ner, link and validate stages; hierarchical
  facets per scheme; knowledge-model, pack and concept pages; query
  tagging; W3C Reconciliation API; curator deltas exported as bundle
  fragments; feed, mail, archive and table connectors; the demo pack and
  `oss demo`

- Phase 1 exit test passed on 2026-10-15: fresh clone, `./oss init`, `./oss up`,
  `./oss index`, `./oss search`, UI and API docs answering through Caddy

- Browser UI (React 19, TypeScript, Vite, Tailwind 4): sign-in, search with
  facets, pagination, highlighted snippets and thumbnails, document page with
  text per page, page previews, found entities and pipeline stage runs
- Session auth with CSRF for the UI; web connector (pages, sitemap, bounded
  crawl); upload connector with `POST /collections/{slug}/upload` and
  `oss upload`; preview stage (pypdfium2 pages, Pillow thumbnails) and
  `GET /documents/{id}/preview/{page}`
- Phase 1 vertical slice: Solr 10 `documents` configset created on first
  start; filesystem connector; fetch, extract (Tika 3 with OCR and language
  detection), structure, patterns and index stages; Django server with
  collections, sources, documents, search, suggest and stats endpoints
  behind bearer tokens; Celery tasks for discovery, processing, reindex and
  deletion; `oss index`, `oss search`, `oss stats`
- Phase 0 foundations: repository layout, licence, decision records, uv
  workspace, core contracts, compose skeleton with pinned services, base
  pack v0.1, demo corpus builder, CI.
