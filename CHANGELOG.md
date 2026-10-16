# Changelog

All notable changes are recorded here. The format follows Keep a Changelog
and the project follows semantic versioning.

## [Unreleased]

### Security

- Every API route has an explicit role rule, checked by a test that walks
  each route as six kinds of user; annotations need the curator role (or
  authorship), document reindex the editor role, and marking pack deltas
  exported is a POST for editors.
- Reading can be limited per collection: with `SEMASCOPE_DEFAULT_ROLE=none`
  a user reads only the collections they hold a role on, across search
  (all modes and exports), documents, previews, the graph, curation and
  stats. The default stays `reader`, so existing installs read as before.
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

- A rejected tag stays on the document, flagged, instead of being removed;
  search and the graph leave it out through the same curated view.
- CI validates `packs/base` and `packs/demo/pack` without rewriting their
  manifests; the base pack's stale manifest is regenerated.

### Fixed

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
