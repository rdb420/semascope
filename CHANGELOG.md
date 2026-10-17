# Changelog

All notable changes are recorded here. The format follows Keep a Changelog
and the project follows semantic versioning.

## [Unreleased]

### Security

- Any setting can be read from a file named by `<NAME>_FILE`; `oss init
  --secrets-dir` and `compose.secrets.yaml` keep every secret out of `.env`.
  `./oss rotate` changes secrets in PostgreSQL, Neo4j, Solr, the admin
  account, the API token and Grafana together with `.env`.
  `SEMASCOPE_PSEUDONYM_KEY` keys graph pseudonyms apart from the secret key and
  is never rotated. Tika runs on an internal network shared only with the
  worker and the cli; Caddy reaches only the server. Build bakes fail the
  build.
- Deleting a document, source or collection also removes its graph nodes,
  blobs, previews, work files and uploads; a daily `gc` sweeps leftovers.
  Alert events in the outbox carry no owner, search name or hits; webhook
  deliveries and alert checks take a lease, so no event is sent twice.
- Feed tokens open feeds only and read as their owner; originals are read
  only from the blob store, and active content is sent as an attachment
  with a sandbox content-security policy; curator controls show only to
  curators.
- Every API route has an explicit role rule, checked by a test that walks
  each route as six kinds of user; annotations need the curator role (or
  authorship), document reindex the editor role, and marking pack deltas
  exported is a POST for editors.
- Reading is denied by default: a user reads only the collections they
  hold a role on, across search (all modes and exports), documents,
  previews, the graph, curation and stats. `SEMASCOPE_DEFAULT_ROLE` defaults
  to `none` and `oss init` writes it; set `reader` to open reading to every
  signed-in user.
- Local sign-up is closed; single sign-on creates accounts only while
  `SEMASCOPE_OIDC_AUTO_SIGNUP` is true and gives them
  `SEMASCOPE_OIDC_DEFAULT_ROLE` (default `none`).
- The server, workers and beat refuse to start with an empty or placeholder
  secret outside debug mode; no secret has a default value; Valkey requires
  `VALKEY_PASSWORD` (`./oss up` adds one to older `.env` files) and Grafana
  `GRAFANA_PASSWORD`.
- A collection's `settings.pipeline` accepts tuning keys only; service
  addresses and credentials come from the deployment.
- Search field names are validated, every value in a Solr query is quoted,
  and collection slugs must be slugs, including where they name folders.
- Webhooks use the outbound address guard and never follow redirects; the
  guard accepts only globally routable addresses, and `max_bytes` has a
  1 GiB ceiling. Custom `x_` fields from sources cannot replace the index's
  own fields; RDF sources refuse JSON-LD with remote contexts; source
  listings mask account identifiers for users below editor.
- Releases are published only from `v*` tags, after `ci` and `integration`
  pass on the tagged commit; every GitHub action is pinned by commit SHA
  with least-privilege permissions; `pip-audit` and `pnpm audit` fail the
  build; a licence gate refuses GPL and AGPL dependencies, and LGPL ones
  unless an ADR accepts them (psycopg, ADR-0011). The `-gpu` image has an
  SBOM.
- Solr requires basic authentication, configured from `SOLR_PASSWORD` at
  start, including on existing installs; backup, restore and `oss doctor`
  send the credentials.
- File sources read only under `SEMASCOPE_SOURCE_ROOTS`; web, feed, SPARQL
  and S3 sources share one private-address guard
  (`SEMASCOPE_ALLOWED_PRIVATE_HOSTS` names exceptions) and response size
  caps; source credentials stay in the source settings and are masked in
  API responses.
- A decided review answers 409 to a second decision.

### Added

- `./oss upload`, `pack import|diff|validate`, `eval` and `export --out`
  work with files on the host; `./oss init` works without uv. `oss doctor`
  checks PostgreSQL, Valkey, the Solr collections and SolrCloud mode,
  versions and the base pack. Health checks for the worker, beat and Caddy.
  The observability profile has traces in Jaeger, a Grafana dashboard and
  Flower on loopback behind a password. `.env.example` lists every setting.
- Per-language search for 28 languages (ar bg ca cs da de el en es fa fi fr
  hi hu id it ja ko lv nl no pl pt ro ru sv tr zh): each document is also
  indexed with its language's stop words and stemmer, Chinese, Japanese and
  Korean as CJK bigrams. Query-time synonyms from the active packs. A
  documented search syntax (`docs/search.md`) with `exact:`, `title:` and
  `text:`, a match option and a word-form switch. The search page suggests
  titles, sorts (newest first without words), leaves facet values out, shows
  more values, lists the filters in force, drills down the timeline, shows
  folder-path trees and entity chips. List search tries the phrase, then all
  words, then near spellings.
- Per-collection `ocr_languages`; the text of embedded items (attachments,
  archive members, images in Office files) is indexed with their container;
  `folder_paths` and `extension_s` fields from source paths; ADR-0013
  deletion and retention.
- Bulk tagging: a curator accepts or rejects a concept on every document of
  a keyword search in a collection (at most 10,000), as a job with one
  proposal per document (`POST /api/curation/bulk-tags`, with a dry run).
- Pack imports and activations run as jobs that the pages and `oss pack`
  follow (`/api/knowledge/pack-jobs`); a plain list of names imports as a
  pack (`oss pack import-list`); polyhierarchy gives a concept a facet path
  through each broader concept, and `skos:related` is kept and shown.
- More like this (`GET /api/documents/{id}/similar`), RDF exports of
  documents and searches in Turtle and JSON-LD (`oss export`), W3C Web
  Annotation export and import (including Hypothesis exports), an OpenSearch
  description for browsers, an Overview page, and `oss eval` relevance
  scores with demo judgements (ADR-0012).
- CI jobs for the web UI (lint, type-check, tests, bundle budget), the supply
  chain, workflow linting and the README quick start on a fresh clone; a
  pull request template with the clean-room confirmation.
- Original files: `GET /api/documents/{id}/original` serves the stored
  original to readers of its collection, with its file name, byte ranges
  and an ETag; PDFs, images, plain text, audio and video open in the
  browser, HTML, SVG and other types download.
- The document page highlights the words of the search that led to it and
  jumps to the first matching page (`GET /api/documents/{id}/matches`).
- Table, entities and graph-of-results views of a search; clicking a graph
  edge lists the documents behind it.
- Alerts page, and Atom and RSS feeds of a saved search, any search and all
  alerting searches, read with a revocable feed token.
- `oss stats` and `oss doctor` report documents whose graph projection is
  pending or failed, and documents held after repeated worker crashes.
- Integration tests against real web, feed, S3 (MinIO), SPARQL (Oxigraph),
  webhook and OpenID Connect services; `docs/assess/evidence.yaml` maps every
  capability-matrix row to the tests that prove it, and a test keeps the two
  in step.
- Every tag explains itself: the stage version, the dictionary label and
  label type that matched (and its language), the NER model, and the named
  entity that linking replaced (`linked_from`); each occurrence records its
  page and the label it matched. `GET /api/curation/documents/{id}/tags`
  returns occurrences and provenance, and `…/tags/history?iri=` every
  decision on a tag. The document page highlights a tag's occurrences and
  shows a "why this tag" panel.
- Every decision on a review item, deferrals included, is kept
  (`ReviewDecision`, migration `curation.0003`); a decision keeps the tag it
  was taken on and its pack version; graph edges carry `stage_version` and
  `pack_version`.
- Packs: `oss pack validate` and import verify `release-manifest.json`
  (a mismatch is an error unless `--force`), run SHACL over the pack's own
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
- Integration tests against the real stack (`tests/integration`,
  `just integration`), the browser journey and a backup and restore round
  trip, in a CI workflow on amd64 and weekly on arm64; the clean-room check
  compares against the pinned legacy sources.
- `oss demo --only`, `SEMASCOPE_ENV_FILE` for a second stack beside the
  first.
- The demo corpus is pinned by hash; `packs/demo/derive.py` builds the
  `mixed-1k` corpus in twelve formats; benchmark scripts for size, review
  throughput and failure recovery, and a hardware block in `bench.py`.
- Compose images pinned by digest (`just bump-images`); release images
  signed with cosign.
- `oss init --size` sets the worker's processes and memory cap;
  `SEMASCOPE_MAP_TILE_URL` (empty for no base map) for offline installs.

### Changed

- Significant terms are scored against the readable collections searched.
  Search parameters are bounded (page_size 0–100, page 1–500, facet_limit
  1–100, no paging past the 10,000th hit); hybrid search fuses the whole
  keyword list. Solr loads the analysis-extras module.
- Pack import and activate answer 202 with a job, after the same plan check
  and 409. Curation decisions commit to PostgreSQL and the outbox first;
  Solr and Neo4j are written after the commit, and `retry-proposals`
  (every 5 minutes) applies what a store missed. The dictionary sync adds
  new entries before removing old ones. A `released` pack without
  `release-manifest.json` is an error.
- `compose.yaml` defaults the image tag to the checkout's version instead of
  `main`; `:main` is no longer published.
- The web UI loads pages and the map, graph and chart libraries on demand:
  about 114 kB of gzipped JavaScript on a first visit instead of 697 kB.
- Worker loss no longer loses or strands documents: a dead pool process
  hands its task back, `recover` (every 2 minutes) requeues documents left
  `processing`, and a redelivered task for a document already indexed from
  the same content and settings does nothing. A document whose runs kill
  the worker three times (`SEMASCOPE_MAX_ATTEMPTS`) is held as failed until
  it changes or `oss reindex`. Document tasks have a hard time limit
  (`SEMASCOPE_TASK_TIME_LIMIT_S`, 1800 s, 5400 s with MinerU) and Valkey's
  visibility timeout follows it.
- A Neo4j outage leaves documents searchable with `graph_status: pending`;
  the projection is written once Neo4j answers (migration `catalogue.0004`).
- Worker pool processes are replaced once they grow past their share of the
  memory cap (`SEMASCOPE_WORKER_MAX_MEMORY_MB` overrides it).
- `oss-worker --queues` defaults to `io,cpu`; stages no longer declare a
  queue (ADR-0010). Stopping the worker hands unfinished tasks back after
  45 s.
- The server runs its start-up preparation in a child process, so the web
  server never holds the pack store open across a fork.
- A rejected tag stays on the document, flagged, instead of being removed;
  search and the graph leave it out through the same curated view.
- CI validates `packs/base` and `packs/demo/pack` without rewriting their
  manifests; the base pack's stale manifest is regenerated.

### Fixed

- `./oss upload` of a host file; the shim creates the import folder before
  Docker can create it as root; the server no longer runs healthy without its
  Solr collections; restore puts the Solr configsets back from the image
  after any RESTORE; `DATABASE_URL` passwords are URL-decoded.
- An explicit OR no longer behaves as AND. English stop words and stemming
  are no longer applied to every language. A numeric query such as `1984`
  survives the search page; the media view counts its pages; a failed CSV
  export is reported; saved searches keep their mode, view and options.
- Backup and restore keep every Solr configset file: configsets have no
  subfolders, because a Solr backup drops them.
- Source runs no longer lose documents: a run deletes what its source no
  longer has only when it read everything it tried, did not stop at a limit
  and saw at least half the documents; a site, feed or sitemap that cannot
  be read fails the run; feeds keep articles that roll off; a deleted
  document comes back when its source has it again; one run per source at a
  time (409 while running).
- The archive connector streams members to disk within one byte budget
  across every nesting level, as documented. Mail URIs include a hash of the
  message and duplicate attachment names are numbered. Blobs are named by
  the hash of what was read. Tika keeps OCR for formats MinerU does not take.
  Titles with several values and regional language codes are read correctly.
  NER labels and SHACL shapes come from the collection's bound packs.
- A rejected concept stayed in the hierarchical facet, even after a reindex,
  and an added one never appeared there. Delta export could be made to emit
  extra triples. The pack triple store was loaded inside the import
  transaction.
- Backup and restore fail loudly at any failed step; restore checks the
  backup and loads it beside the live data before switching, so a corrupt
  backup changes nothing. `oss init --force` keeps every non-secret setting.
- The docs deploy no longer tries to enable Pages itself;
  `cleanroom_check.py` fails when no legacy tree is given.
- `scripts/integration.sh` creates the import folder before its first
  container run, so a fresh checkout no longer gets a root-owned `data/`.
- Single sign-on through OpenID Connect failed at the callback: the image
  lacked PyJWT and cryptography (`django-allauth` now with its
  `socialaccount` extra).
- The pack SPARQL console refused a SELECT or ASK that started with PREFIX
  declarations.
- A rejected tag came back in PostgreSQL after a reindex, and a curator's
  link to a pack individual was undone by one.
- Pack import, SPARQL and version removal failed in all but one server
  process (Oxigraph's lock) and could hang after the server forked; each
  pack-store operation now runs in its own process. SPARQL ASK over a pack
  returned no rows. Re-importing an unchanged pack with activation left it
  inactive.
- Semantic and hybrid search apply every filter to the vector candidates,
  so totals and pages are right when facets are selected; deletion removes
  a document's passages too.
- Curation: the proposal log is append-only and the newest decision on a
  target wins, so accept, reject, accept leaves the tag accepted (rc.2
  returned the first proposal unapplied). Each decision has its own
  `external_ref`, chained to the one it supersedes (`supersedes` in the
  outbox event); repeating the decision in force is a no-op that re-applies
  it if a store missed it. Migration `curation.0002` also drops the
  never-produced kinds (review `delta`; proposals `add_label`,
  `new_concept`, `merge_entities`).
- `CURATED` edges to named entities use the graph stage's key
  (`<Class>/<label>`, or its HMAC when the collection pseudonymises), so
  they land on the node the machine linked.
- MinerU receives PDF, OOXML Word, PowerPoint and Excel, and raster image
  files, chosen by media type; rc.2 sent only images.
- The worker initialises OpenTelemetry in every pool process, and server,
  worker and beat export metrics as well as traces (`semascope.documents`,
  `semascope.stage.duration`, plus the instrumentation's own).
- `GET /api/suggest` is routed (rc.2 listed it but never routed it).
- Transient failures of remote providers, Solr, Neo4j, Tika and MinerU are
  retried: twice in place (2 s, 4 s), then the document task up to three
  times (60, 120, 240 s); permanent failures are not retried and nothing
  falls back to a CPU model. A failed fetch no longer leaves a document
  in `processing`.
- The `chunks` index has a vector field per dimension (256, 384, 512, 768,
  1024, 1536, 2560, 3072, 4096), so embedding models other than 384
  dimensions work; `oss doctor` reports a model the schema cannot hold
  (new optional `SEMASCOPE_EMBED_DIMENSIONS`).
- `CSRF_TRUSTED_ORIGINS` follows `SEMASCOPE_HTTPS_PORT` (new setting,
  default 8443) instead of assuming 443 and 8443.
- Pack conflicts resolve by binding precedence: an IRI in two active packs
  no longer breaks `GET /api/knowledge/concepts?iri=` (which takes an
  optional `collection`), and the tag and link stages choose the
  higher-precedence pack instead of Solr's order.
- Curator draft labels tag only the collection that proposed them and
  survive dictionary syncs (new `collection_scope` field in the `entities`
  schema).
- A dictionary tag's confidence is the best label type over all its
  occurrences, not that of its first one.
- `/healthz` no longer reports a stale `phase`; stale phase notes removed
  from docstrings, `deploy/neo4j/README.md` and the plan; the concepts page
  lists the `s3` and `rdf` connectors.

### Upgrade notes

- `./oss up` adds `SEMASCOPE_PSEUDONYM_KEY` to an older `.env` as a copy of the
  old secret key, so existing pseudonyms stay the same.
- Run `./oss reindex --collection <slug> --stage index` for each collection to
  fill the per-language fields; synonyms need no reindex.
- Existing mail documents are re-created once under their new URIs.
  Alert webhook payloads are now `{saved_search, new, total}`.
- Run migrations; reindex collections so their tree facets come from the
  curated view; re-activate packs to fill multi-parent paths. Clients of the
  pack import and activate API must follow the returned job.
- Reading is now per collection by default: set `SEMASCOPE_DEFAULT_ROLE=reader`
  in `.env` to keep open reading, or give users roles.
- Run `./oss up` (not plain `docker compose up`) once: it adds
  `VALKEY_PASSWORD` to `.env`. The app refuses to start with an empty or
  placeholder secret; the observability profile needs `GRAFANA_PASSWORD`.
- Restore needs free disk for a second copy of the Solr collections and the
  database while it runs. Sources with `max_bytes` above 1 GiB are clamped.
- `./oss up` runs the migration and uploads the new `entities` and
  `chunks` configsets. To move existing draft labels to their collection,
  re-activate each pack that has `add_label` deltas
  (`POST /api/knowledge/packs/{id}/activate?version=`), which re-syncs its
  dictionary and queues a reindex of the collections that use it; until
  then, drafts written by rc.2 still tag every collection. Existing 384-dimension chunks stay in the `vector` field and
  need no reindex; after switching to a model of another dimension, run
  `./oss reindex --collection <slug> --stage embed` for each collection.

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
