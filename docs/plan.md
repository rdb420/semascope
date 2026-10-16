# Development plan

Five phases to 1.0. Each phase ends with a demonstration on a clean machine
from a fresh clone, and that demonstration is the gate.

| Phase | Deliverable | Exit criterion |
|---|---|---|
| 0 Foundations (done 2026-10-15) | Repository, licence, decision records, uv workspace, core contracts, compose skeleton with pinned services, CI, base pack v0.1, demo corpus builder | `./oss up` on a clean Ubuntu VM brings every service up healthy; CI green; base pack passes the ontology skill's deterministic gates |
| 1 Vertical slice (done 2026-10-15) | Filesystem, web and upload connectors; extract, OCR, structure, patterns, preview and index stages; Solr 10 configsets; Celery on Valkey; search API; SPA with search, facets, list and preview; local accounts; `oss index` | Index a folder of mixed files including scanned PDFs and search them with highlighted previews from a fresh clone |
| 2 Knowledge model (done 2026-10-15) | Pack importer, Oxigraph store, entities collection, Tagger stage, hierarchical facets, NER, linking, SHACL, query tagging, pack pages, editor with deltas, Reconciliation API, feed, mail, archive and table connectors, reindex | Import two packs, tag the golden corpus, navigate hierarchical facets, export a delta the skill can ingest; tagging precision and recall recorded |
| 3 Graph and exploration (done 2026-10-15) | Neo4j projection, graph API and view, map, timeline, terms, media grid, table export, curation, proposals and outbox, webhooks, saved searches, list search, S3 and RDF connectors | A review decision flows to the outbox and to Neo4j; a path between two entities shows in the UI; browser journeys pass |
| 4 Semantic layer and 1.0 (release candidates 2026-10-15 and 2026-10-16) | Embeddings, chunks collection, hybrid search, optional LLM answers, OIDC, roles per collection, audit log, backup and restore, OpenTelemetry, runtimes `cpu`, `gpu` and `remote` with a provider per heavy stage (ADR-0008), arm64 images, security review, complete documentation | 1.0 on GHCR with signed images; a stranger follows the quick start to a working instance; search and tagging quality published |

Deliberately after 1.0: Helm chart, OpenSearch backend, SPLADE, Docling
extractor, multi-instance scaling, SaaS multi-tenancy. (GLiNER, first listed
here, shipped in 1.0.0-rc.2 as the `gliner` entity provider.)
