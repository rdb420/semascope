# Explainability model

Status: **draft**, 2026-10-16. Describes the 1.0 record (branch
`hardening/explain`, after 1.0.0-rc.3); see
[ADR-0009](../adr/0009-explainability-record.md).

Every link between a document and a concept or entity should answer five
questions: **where** in the text it came from, **how** it was found, **how
sure** the machine was, **which version** of the knowledge model it used,
and **what people decided** about it since. This page sets out what
Semascope records for each question, where the record is kept, and what is
still missing.

## The link record

The pipeline records each link as a `Tag` (`oss_core.contracts`), kept in
full on the document in PostgreSQL (`catalogue.Document.tags`, JSON). Every
field after `confidence` is optional, so tags stored before 1.0 still load
(they have the fields empty until the next reindex).

| Field | Meaning |
|---|---|
| `iri` | the concept or entity linked to |
| `label` | its display label (the preferred label for pack terms) |
| `kind` | `concept` or `entity` |
| `stage`, `stage_version` | the stage that produced it (`tag`, `ner`, `patterns`, or `curated` for a person) and that stage's code version |
| `confidence` | 0 to 1 (see below for what it means per stage) |
| `pack_id`, `pack_version` | the pack version whose dictionary or individual it resolved to; empty for links that do not come from a pack |
| `matched_label`, `label_type`, `lang` | dictionary and linked tags: the label that matched and its type (`pref`, `alt`, `hidden` or `draft`), the one that gave the confidence; `lang` is the language the pack records for the entry |
| `model` | NER tags: `provider:model` (for example `spacy:en_core_web_sm`) |
| `linked_from` | `{iri, label, confidence, model, link_version}`: the named entity the link stage (or a curator) replaced with a pack individual |
| `spans[]` | `{start, end, page, matched}`: character offsets into the document's extracted text, one per occurrence; `page` is 1-based; `matched` is the dictionary label this occurrence matched (dictionary matches only) |

Beside the tags, each document keeps:

- `stage_runs[]`: `{stage, version, started_at, duration_ms, outcome,
  message}` for every stage of the last run.
- `fields.page_starts`: where each page starts in the text (written by the
  `structure` stage, which joins pages with a blank line); span pages come
  from it.
- `fields.ner_model`: `provider:model` of the NER run.
- `fields.curated`: the decision in force per IRI (`accept` or `reject`;
  for a curator's link also `linked_from`, label and pack version).
- `fields.shacl_violations`: SHACL results, when the `validate` stage ran.

## How each kind of link is produced

| Producer | Method | Confidence | Pack version |
|---|---|---|---|
| `tag` stage | dictionary match through Solr's Tagger against the active packs' labels | fixed by label type: preferred 1.0, alternative 0.9, curator draft 0.85, hidden 0.75; the best type over all occurrences, which is the one `label_type` and `matched_label` name | recorded; an IRI in two packs goes to the pack with the higher binding precedence |
| `ner` stage, `spacy` | statistical NER over paragraph windows | fixed 0.7 (spaCy gives no score) | none (not from a pack) |
| `ner` stage, `gliner` or `remote` | zero-shot NER, labels from the base four classes and the active packs' classes | the model's score (highest over the occurrences) | none (not from a pack) |
| `link` stage | an NER entity whose normalised text equals a label of a pack individual becomes that individual; `stage` stays `ner`, `linked_from` keeps the entity | raised to at least 0.85 | recorded |
| `patterns` stage | regular expressions (email, URL, phone, amount, hashtag) | fixed 0.9 | none (not from a pack) |
| a curator | accept, reject, add, remove, or link to an individual | 1.0 for added and linked tags | the pack version that defines the IRI, by the collection's precedence |

When the link stage finds that the individual was already tagged another
way (usually by the dictionary), the NER occurrences join that tag, which
keeps its own provenance, records the entity in `linked_from`, and has
spans without `matched` for the NER occurrences.

A tag below 0.8 confidence, an unlinked entity that resembles a pack
individual, and a SHACL violation each open a **review item**.

## Human decisions

A decision on a review item, or directly on a document's tags, is written
in four places:

1. **`Proposal`** (PostgreSQL): `kind`, `target`, `params`, `reason`,
   `decided_by`, `decided_at`, a stable `external_ref`, and `pack_id` and
   `pack_version` of the tag decided (for a link, of the individual). The
   log is append-only: each decision is a new row, the newest one is in
   force, and the outbox event names the one it supersedes. `params.tag`
   keeps the tag as it was decided (stage, confidence, provenance, up to
   200 spans and `span_count`), so a rejected tag's evidence is never lost.
   The snapshot is not part of `external_ref`.
2. **`ReviewDecision`** (PostgreSQL): every decision on a review item, a
   deferral included, with who, when, the note and the proposal it became.
   The `Review` row keeps the decision in force; a decided review answers
   409 to another decision. Reviews decided before 1.0 have one history row,
   made from their own fields by migration `curation 0003_review_decisions`.
3. **`Event`** (the outbox, `GET /api/curation/events`): a `proposal.*`
   event carrying the same facts, delivered to webhooks with an HMAC
   signature.
4. **The stores:** `Document.fields.curated`, the Solr field `curated_ss`
   (`iri:accept|reject`), and a `CURATED {decision, by, at, note}` edge in
   Neo4j beside the machine's edge.

### Rejected tags are kept, flagged

`Document.tags` is the machine's record of the last run. A tag people
rejected **stays in it**, flagged by `fields.curated`, both right after the
decision and after any reindex (the pipeline writes what it finds; it never
drops a rejected tag). Only a tag that a curator had added, and that has no
machine evidence, is removed when rejected; its proposal keeps it.

All three stores apply the decisions through one function,
`oss_pipeline.provenance.curated_tags`:

| | PostgreSQL (`/tags` API) | Solr | Neo4j |
|---|---|---|---|
| rejected tag | listed with `rejected: true` and its evidence | left out of the facets; `curated_ss` has `iri:reject` | no `TAGGED`/`MENTIONS` edge; `CURATED {decision: reject}` |
| accepted addition | listed, `stage: curated` | in the facets | edge with `stage: curated` |
| curator's link | listed under the individual, `linked_from` the entity | individual in `entity_iris_ss` | `MENTIONS` to the individual after the next reindex; `CURATED` at once |

## Where each part of the explanation is kept

| | PostgreSQL | Solr `documents` | Neo4j |
|---|---|---|---|
| link exists | `Document.tags` | `concept_iris_ss`, `entity_iris_ss` (pack-linked entities), entity facets | `TAGGED`, `MENTIONS` |
| spans, pages, matched label per occurrence | yes | no | count only (`count`) |
| stage and stage version | yes | no | `stage`, `stage_version` on the edge |
| matched label, label type, model, `linked_from` | yes | no | no |
| confidence | yes | no | `confidence` on the edge |
| pack version | yes, per tag | `pack_versions_ss` per document | `pack_version` on `TAGGED` and on `MENTIONS` of individuals; `(:Entity)-[:DEFINED_IN]->(:Pack)` |
| human decision | `Proposal` (with the tag snapshot), `ReviewDecision`, `Review`, `fields.curated` | `curated_ss` | `CURATED` |

PostgreSQL holds the full record; Solr and Neo4j hold what search and
navigation need. Spans and provenance are kept out of Solr on purpose.

## What a person sees

- **Document page:** the tags, each with stage, stage version, confidence
  and pack version in its tooltip, and accept and reject buttons; a
  rejected tag is struck through. Selecting a tag highlights its
  occurrences in the text (with previous and next) and opens a **why this
  tag** panel: status, method and stage version, matched label and label
  type, confidence and what it means for that method, pack id and version,
  model, `linked_from`, the pages it occurs on, and the decision history
  (every proposal and review decision, oldest first, the one in force
  marked, with the evidence each decision was taken on).
- **Review queue:** kind, label, confidence, stage and number of
  occurrences, with the candidate individuals for a link item.
- **Graph:** `CURATED` edges appear beside machine edges in a neighbourhood.
- **API:** `GET /api/curation/documents/{id}/tags` (tags with spans,
  provenance, the decision in force and `rejected`),
  `GET /api/curation/documents/{id}/tags/history?iri=` (proposals and review
  decisions for one link; a link decision made on an entity is found from
  the individual too), `GET /api/curation/proposals`,
  `GET /api/curation/events`. Both document routes need read access to the
  collection.

## Gaps

| Question | Gap |
|---|---|
| how sure | Dictionary and spaCy confidences are fixed per method, not calibrated. |
| how | `lang` is the language the pack records for the concept, not a per-label language tag. Tags stored before 1.0 have no provenance fields until their collection is reindexed. |
| which version | Pattern and NER tags have no pack version, by nature. A curator's decision records the pack version current at the time; it does not follow a later pack upgrade. |
| what people decided | A decision's snapshot keeps at most 200 spans (`span_count` has the total). Reviews decided before 1.0 have only their last decision in the history. After a curator's link, the graph's `MENTIONS` edge moves to the individual at the next reindex, not at once. |

## Defects fixed on the way to 1.0

1. Reversing a decision and then reversing it back (accept, reject,
   accept) returned the first stored proposal without applying it again.
   The proposal log is now append-only (rc.3).
2. For named entities that are not pack individuals, the `CURATED` edge was
   written to a node keyed by the full IRI. It now uses the graph stage's
   key (rc.3).
3. A rejected tag was removed from `Document.tags` by the decision and came
   back with the next reindex, so PostgreSQL disagreed with itself and with
   Solr and Neo4j. Rejected tags are now always kept, flagged.
4. A curator's link rewrote the stored NER tag, which the next reindex
   undid (the link then reached neither Solr nor the graph). The link is
   now part of the curated view, applied on every run.
