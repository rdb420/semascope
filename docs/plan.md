# Development plan

Five phases to 1.0. Each phase was to end with a demonstration on a clean
machine from a fresh clone. Status as of 2026-10-17, for 1.0.0-rc.3
(unreleased).

| Phase | Deliverable | Exit criterion | Status |
|---|---|---|---|
| 0 Foundations | Repository, licence, decision records, uv workspace, core contracts, compose skeleton with pinned services, CI, base pack v0.1, demo corpus builder | `./oss up` on a clean Ubuntu VM brings every service up healthy; CI green; base pack passes the ontology skill's deterministic gates | delivered 2026-10-15. The exit was recorded on the development host, not a clean VM; the base pack passes `oss pack validate`, which implements the skill's gates ([ADR-0007](adr/0007-pack-contract.md)) |
| 1 Vertical slice | Filesystem, web and upload connectors; extract, OCR, structure, patterns, preview and index stages; Solr 10 configsets; Celery on Valkey; search API; SPA with search, facets, list and preview; local accounts; `oss index` | Index a folder of mixed files including scanned PDFs and search them with highlighted previews from a fresh clone | delivered 2026-10-15 |
| 2 Knowledge model | Pack importer, Oxigraph store, entities collection, Tagger stage, hierarchical facets, NER, linking, SHACL, query tagging, pack pages, editor with deltas, Reconciliation API, feed, mail, archive and table connectors, reindex | Import two packs, tag the golden corpus, navigate hierarchical facets, export a delta the skill can ingest; tagging precision and recall recorded | delivered 2026-10-15; document-level tagging precision and recall recorded ([Quality](quality.md)); no record of the skill ingesting an exported delta |
| 3 Graph and exploration | Neo4j projection, graph API and view, map, timeline, terms, media grid, table export, curation, proposals and outbox, webhooks, saved searches, list search, S3 and RDF connectors | A review decision flows to the outbox and to Neo4j; a path between two entities shows in the UI; browser journeys pass | delivered 2026-10-15; the browser journey (`tests/e2e/ui_smoke.py`) runs in `just integration` and the `integration` workflow |
| 4 Semantic layer and 1.0 | Embeddings, chunks collection, hybrid search, optional LLM answers, OIDC, roles per collection, audit log, backup and restore, OpenTelemetry, runtimes `cpu`, `gpu` and `remote` with a provider per heavy stage (ADR-0008), arm64 images, security review, complete documentation | 1.0 on GHCR with signed images; a stranger follows the quick start to a working instance; search and tagging quality published | in progress: release candidates 1.0.0-rc.1 and rc.2 (2026-10-15, 2026-10-16); see below |

## Phase 4: what is done and what is left

Done on the main line (1.0.0-rc.3, unreleased; the [changelog](https://github.com/rdb420/semascope/blob/main/CHANGELOG.md)
has the detail): embeddings, the `chunks` collection with a vector field per
dimension, semantic and hybrid search; single sign-on with closed sign-up;
roles per collection with reading denied by default; the audit log; backup
and restore that fail loudly and restore safely; OpenTelemetry traces and
metrics with a Grafana dashboard; the three runtimes; a security review of
1.0.0-rc.2 and the fixes it led to (rate limits, sign-in lockout, the
outbound address guard, secrets from files and rotation, a network split,
security headers); search relevance numbers (`oss eval`); the documentation
on this site.

Left before 1.0:

- **A release.** Tag a version so that the `release` workflow runs for the
  first time: CI and integration on the tagged commit, multi-arch images with
  SBOM and provenance, cosign signatures, the docs site on GitHub Pages. None
  of these has run yet; the owner's repository settings it needs are in
  [Operations](operations.md#repository-settings-the-release-needs).
- **The workflows on GitHub.** The `ci` and `integration` workflows,
  including the fresh-clone job that runs the README quick start, have not
  yet run for the hardening work.
- **A stranger's quick start.** Not yet done by anyone outside the project.
- **Span-level tagging and named-entity quality.** Not measured; they need a
  labelled set from curators' decisions ([Quality](quality.md)).
- **The owner's open decisions** in [Decisions](assess/decisions.md), and
  accepting or superseding the proposed ADRs.

**Optional LLM answers** were part of Phase 4 and are not built: the
settings exist, nothing reads them. They are deferred until after 1.0.

## Deliberately after 1.0

Helm chart and multi-host scaling (one Compose project on one host for 1.0),
an OpenSearch backend (the `IndexBackend` protocol is defined but nothing
implements it), SPLADE and other sparse vectors, the Docling extractor,
SaaS multi-tenancy, optional LLM answers, merging entities, entity pages for
named entities that are not pack individuals, managing sources and
collections in the UI, a UI in other languages, and the capabilities still
open in [ADR-0012](adr/0012-never-delivered-capabilities.md). GLiNER, first
listed here, shipped in 1.0.0-rc.2 as the `gliner` entity provider.
