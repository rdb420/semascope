# Semascope

Semascope turns a pile of documents and a knowledge model into a searchable,
navigable, reviewable knowledge base, on your own hardware.

- **Index** files, web pages, feeds, mail, archives, tables, buckets and RDF.
- **Enrich** against knowledge models packaged as [packs](concepts.md#pack):
  dictionary tagging, hierarchical facets, named-entity recognition, entity
  linking, SHACL validation, embeddings.
- **Explore** with faceted, semantic and hybrid search, concept and entity
  pages, previews, a map, a timeline and a document-entity graph in Neo4j.
- **Curate** through review queues whose decisions are logged and exported as
  proposals for the system that owns the data.

## What you get

- **Ingest** from folders, uploads, web pages and sitemaps, RSS and Atom,
  mailboxes, archives, spreadsheets, S3 buckets, RDF, web archives,
  Hypothesis and Mastodon, with OCR for scans, now or later.
- **Enrich** against knowledge models packaged as ontology-skill bundles:
  dictionary tagging with hierarchical facets, named entities linked to your
  individuals, SHACL validation, chunk embeddings.
- **Search** by keyword, by meaning, or both, with facets, highlighted
  snippets, passages, a timeline, a media grid, terms, batch name lists and
  CSV export.
- **Explore** concept and entity pages, the document-entity graph in Neo4j
  with paths between entities, and a map of your places.
- **Curate** through a review queue; every decision is applied at once,
  survives reindexing, and leaves through the outbox and webhooks for the
  system that owns your data.
- **Run it yourself**: one `init`, one `up`; local accounts or single
  sign-on, roles per collection, backup and restore, optional observability,
  and the heavy stages on CPU, on a local GPU or through remote services.

Start with [Install](install.md), then read [Concepts](concepts.md).
[Connector settings](connectors.md), [Languages and OCR](languages.md),
the [API](api.md), [events and webhooks](events.md) and
[writing a plugin](plugins.md) are the references; the
[development plan](plan.md), [quality numbers](quality.md),
[security](security.md), [operations](operations.md), the
[assessment pages](assess/index.md) and the
[decision records](adr/index.md) are here too.

This site describes 1.0.0-rc.3, which is not released yet; what is
implemented, experimental or planned is in the
[capability matrix](assess/capabilities.md).
