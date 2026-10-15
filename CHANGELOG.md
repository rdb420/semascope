# Changelog

All notable changes are recorded here. The format follows Keep a Changelog
and the project follows semantic versioning.

## [Unreleased]

### Added

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
