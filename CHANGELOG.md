# Changelog

All notable changes are recorded here. The format follows Keep a Changelog
and the project follows semantic versioning.

## [Unreleased]

### Added

- Phase 1 vertical slice: Solr 10 `documents` configset created on first
  start; filesystem connector; fetch, extract (Tika 3 with OCR and language
  detection), structure, patterns and index stages; Django server with
  collections, sources, documents, search, suggest and stats endpoints
  behind bearer tokens; Celery tasks for discovery, processing, reindex and
  deletion; `oss index`, `oss search`, `oss stats`
- Phase 0 foundations: repository layout, licence, decision records, uv
  workspace, core contracts, compose skeleton with pinned services, base
  pack v0.1, demo corpus builder, CI.
