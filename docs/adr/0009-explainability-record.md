# ADR-0009: Every tag carries its own explanation

Status: proposed.

## Decision

A `Tag` (`oss_core.contracts`) answers five questions on its own: where in
the text it came from, how it was found, how sure the machine was, which
version of the knowledge model it used, and what people decided since. To
that end the contract gains optional fields, so tags stored before 1.0 still
load:

- `Tag.stage_version`: the version of the stage that produced it;
- `Tag.matched_label`, `label_type`, `lang`: the dictionary entry behind a
  dictionary match (the one whose label type gave the confidence);
- `Tag.model`: `provider:model` for named entities;
- `Tag.linked_from {iri, label, confidence, model, link_version}`: the named
  entity the `link` stage replaced with a pack individual;
- `Span.page` (filled from the page boundaries the `structure` stage
  records) and `Span.matched` (the dictionary label one occurrence matched).

People's decisions are kept beside the tags, never in them: the proposal
log (append-only, the newest decision in force) keeps a snapshot of the tag
as it was decided, and every decision on a review item is a
`ReviewDecision` row.

`Document.tags` is the machine's record. A tag people rejected stays there,
flagged by `fields.curated`, as a reindex leaves it; Solr and Neo4j are
written from one curated view (`oss_pipeline.provenance.curated_tags`) that
leaves rejected tags out and applies accepted additions and curators' links.

## Context

The record answered "which pack version" per tag and "which code version"
per document, but not which label matched, which model found an entity,
what a linked entity was before linking, or what was rejected and why.
Rejecting a tag deleted it from the document, and a reindex brought it
back, so PostgreSQL disagreed with itself over time and with Solr and Neo4j.

## Consequences

- Spans and provenance stay in PostgreSQL. Solr keeps the facet fields it
  had; graph edges gain `stage_version` and `pack_version` only.
- The tags API returns spans and the provenance fields, and a history
  endpoint returns the decisions on one link; the document page highlights
  a tag's occurrences and explains it.
- Adding a field to `Tag` stays an additive, optional change; removing one
  needs a migration of stored JSON.

## Alternatives

- Drop rejected tags from `Document.tags` on every run. Consistent too, but
  the machine's evidence for a rejected tag would then live only in the
  decision's snapshot, and the document page could no longer show what was
  rejected.
- A separate provenance table per span. Exact, but a join for every read of
  a document and a large table for little gain at this scale.
