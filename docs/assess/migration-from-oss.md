# Coming from Open Semantic Search

Status: **draft**, updated 2026-10-17. Describes 1.0.0-rc.3 (unreleased: the
main line after 1.0.0-rc.2).

## Inspiration, not compatibility

Semascope was inspired by [Open Semantic Search](https://opensemanticsearch.org/)
(OSS): a SKOS thesaurus, dictionary tagging, hierarchical facets, OCR and
a faceted exploration UI in one product. It is a new implementation, not a
fork or a successor release ([ADR-0001](../adr/0001-clean-room-apache-2.md)):

- **No shared code.** No code, Solr configset, template, stylesheet, script,
  image or documentation text was copied. Semascope is Apache-2.0; OSS is
  GPL-3.0.
- **No software compatibility.** Semascope does not read an OSS Solr index,
  does not accept OSS's ETL plugins or configuration, and does not offer
  OSS's URLs or APIs. Its Solr schema was written for Solr 10 from scratch.
- **The `oss` command and the `oss-*` package names** are this project's
  working abbreviation. They do not mean the command works with an Open
  Semantic Search installation.

What carries over is the idea: index a corpus, tag it against a controlled
vocabulary, and browse it through facets. Concepts map roughly like this:

| Open Semantic Search | Semascope |
|---|---|
| Thesaurus (SKOS), named entities, ontologies | a [pack](pack-spec.md): SKOS schemes and concepts, OWL classes, named individuals, SHACL shapes |
| Dictionary-based tagging in the ETL | the `tag` stage (Solr Tagger) |
| spaCy NER service | the `ner` stage (spaCy, GLiNER2 or a remote service) |
| Facets from the thesaurus | one hierarchical facet per concept scheme |
| Crawlers and file-system indexing | sources and [connectors](../connectors.md) (folder with sidecar metadata, web with scrape rules, feed, mail, PST, archive, table, S3, RDF, WARC, Hypothesis, Mastodon); `oss watch` for changes as they happen |
| Plugins of the ETL | [connector and stage plugins](../plugins.md) through Python entry points |
| OCR later | `ocr: later` per collection ([Languages and OCR](../languages.md)) |
| Tags and annotations in the UI | review queue, tag decisions, bulk tagging and annotations, logged as proposals and sent through the [outbox and webhooks](../events.md) |
| Linking to the original file | an authorised download of the stored original ([ADR-0016](../adr/0016-original-file-download.md)) |
| Search UI (PHP) | the React UI and the HTTP API |

## Moving a corpus across

There is no importer in 1.0. The supported path is to re-index from the
originals and bring the vocabulary across as a pack:

1. **Documents.** Point a Semascope source at the same files, sites, feeds
   or mailboxes that OSS indexed. Semascope extracts, OCRs and enriches them
   again; nothing is read from the old Solr index.
2. **Thesaurus.** Export the OSS thesaurus as SKOS Turtle, place it in a
   pack as `taxonomy.ttl`, add the other required files of the
   [pack specification](pack-spec.md#file-layout) (they may be minimal),
   run `oss pack validate`, then `oss pack import --activate`. Concepts,
   preferred, alternative and hidden labels and `skos:broader` carry over;
   each concept must be in a `skos:ConceptScheme`. Lists of named entities
   become `instances.ttl` with `tag_individuals: true`.
3. **RDF metadata.** Load RDF files or a SPARQL endpoint with the `rdf`
   connector; each subject becomes a document. Metadata that should drive
   tagging belongs in a pack instead.

What cannot be moved in 1.0:

| Item | Status |
|---|---|
| An existing Solr index | not imported; re-index from the originals |
| Tags and annotations made by people in OSS | annotations kept in Hypothesis can be imported as notes (`POST /api/annotations/w3c/import` takes a Hypothesis or W3C export) or read by the `hypothesis` connector; for tags there is no importer: a CSV or JSON import of `(document URI, IRI, decision)` rows into the proposal log is the likely shape if there is demand |
| Saved searches and bookmarked search URLs | no importer, and the URLs differ: a Semascope search URL keeps its words, filters, view, mode and options in its query string (`/?q=…&collection=…`; see [Decisions](decisions.md), D-6), and the query syntax is in [Searching](../search.md) |
| OSS ETL plugins and configuration | not supported; Semascope stages are Python entry-point plugins |

If you have a corpus with significant manual curation in OSS, tell us in an
issue what it looks like; that decides whether the decision importer comes
in 1.1.
