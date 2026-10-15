# Changelog

All notable changes are recorded here. The format follows Keep a Changelog
and the project follows semantic versioning.

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
