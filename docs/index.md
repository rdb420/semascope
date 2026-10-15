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
  mailboxes, archives, spreadsheets, S3 buckets and RDF, with OCR for scans.
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
  sign-on, roles per collection, backup and restore, optional observability
  and GPU profiles.

Start with the quick start in the README, then read [Concepts](concepts.md).
The [development plan](plan.md), [quality numbers](quality.md),
[security](security.md), [operations](operations.md) and the
[decision records](adr/index.md) are here too.
