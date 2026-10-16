# Coming from Open Semantic Search

Status: **draft**, 2026-10-16. Describes 1.0.0-rc.2 (commit `8e4cb2b`).

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
| Crawlers and file-system indexing | sources and connectors (folder, web, feed, mail, archive, table, S3, RDF) |
| Tags and annotations in the UI | review queue, tag decisions and annotations, logged as proposals |
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
| Tags and annotations made by people in OSS | no importer; a CSV or JSON import of `(document URI, IRI, decision)` rows into the proposal log is the likely shape if there is demand |
| Saved searches | no importer; the query syntax differs |
| OSS ETL plugins and configuration | not supported; Semascope stages are Python entry-point plugins |

If you have a corpus with significant manual curation in OSS, tell us in an
issue what it looks like; that decides whether the decision importer comes
in 1.1.
