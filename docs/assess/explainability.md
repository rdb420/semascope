# Explainability model

Status: **draft**, 2026-10-16. Describes 1.0.0-rc.2 (commit `8e4cb2b`).

Every link between a document and a concept or entity should answer five
questions: **where** in the text it came from, **how** it was found, **how
sure** the machine was, **which version** of the knowledge model it used,
and **what people decided** about it since. This page sets out what
Semascope records for each question today, where the record is kept, and
what is still missing.

## The link record

The pipeline records each link as a `Tag` (`oss_core.contracts`), kept in
full on the document in PostgreSQL (`catalogue.Document.tags`, JSON):

| Field | Meaning |
|---|---|
| `iri` | the concept or entity linked to |
| `label` | its display label (the preferred label for pack terms) |
| `kind` | `concept` or `entity` |
| `stage` | the stage that produced it: `tag`, `ner`, `patterns` or `curated` |
| `confidence` | 0 to 1 (see below for what it means per stage) |
| `pack_id`, `pack_version` | the pack version whose dictionary or individual it resolved to; empty for links that do not come from a pack |
| `spans[]` | `{start, end, page}`: character offsets into the document's extracted text, one per occurrence |

Beside the tags, each document keeps:

- `stage_runs[]`: `{stage, version, started_at, duration_ms, outcome,
  message}` for every stage of the last run. This is the per-document
  record of which code version produced the tags.
- `fields.ner_model`: `provider:model` of the NER run (for example
  `spacy:en_core_web_sm` or `gliner:fastino/gliner2-large-v1`).
- `fields.curated`: the current human decision per IRI.
- `fields.shacl_violations`: SHACL results, when the `validate` stage ran.

## How each kind of link is produced

| Producer | Method | Confidence | Pack version |
|---|---|---|---|
| `tag` stage | dictionary match through Solr's Tagger against the active packs' labels | fixed by label type: preferred 1.0, alternative 0.9, curator draft 0.85, hidden 0.75; the best type over all occurrences | recorded; an IRI in two packs goes to the pack with the higher binding precedence |
| `ner` stage, `spacy` | statistical NER over paragraph windows | fixed 0.7 (spaCy gives no score) | none |
| `ner` stage, `gliner` or `remote` | zero-shot NER, labels from the base four classes and the active packs' classes | the model's score (highest over the occurrences) | none |
| `link` stage | an NER entity whose normalised text equals a label of a pack individual becomes that individual | raised to at least 0.85 | recorded |
| `patterns` stage | regular expressions (email, URL, phone, amount, hashtag) | fixed 0.9 | none |
| a curator | accept, reject, add or remove a tag | 1.0 for added tags | not recorded |

A tag below 0.8 confidence, an unlinked entity that resembles a pack
individual, and a SHACL violation each open a **review item**.

## Human decisions

A decision on a review item, or directly on a document's tags, is written
in three places:

1. **`Proposal`** (PostgreSQL): `kind`, `target`, `params`, `reason`,
   `decided_by`, `decided_at` and a stable `external_ref`. This is the
   decision log.
2. **`Event`** (the outbox, `GET /api/curation/events`): a `proposal.*`
   event carrying the same facts, delivered to webhooks with an HMAC
   signature.
3. **The stores:** `Document.fields.curated`, the Solr field `curated_ss`
   (`iri:accept|reject`), and a `CURATED {decision, by, at, note}` edge in
   Neo4j beside the machine's edge.

Decisions survive reindexing: the index and graph stages apply
`fields.curated`, and the graph stage keeps `CURATED` edges when it rewrites
`TAGGED` and `MENTIONS`.

## Where each part of the explanation is kept

| | PostgreSQL | Solr `documents` | Neo4j |
|---|---|---|---|
| link exists | `Document.tags` | `concept_iris_ss`, `entity_iris_ss` (pack-linked entities), entity facets | `TAGGED`, `MENTIONS` |
| spans | yes, offsets | no | count only (`count`) |
| stage | yes | no | `stage` on the edge |
| confidence | yes | no | `confidence` on the edge |
| pack version | yes, per tag | `pack_versions_ss` per document | `TAGGED.pack_version`; `(:Entity)-[:DEFINED_IN]->(:Pack)` for individuals |
| human decision | `Proposal`, `Review`, `fields.curated` | `curated_ss` | `CURATED` |

PostgreSQL holds the full record; Solr and Neo4j hold what search and
navigation need.

## What a person sees

- **Document page:** each tag with its stage and confidence (tooltip), the
  number of occurrences, accept and reject buttons, a strike-through when
  rejected, and the stage-run log.
- **Review queue:** kind, label, confidence, stage and number of
  occurrences, with the candidate individuals for a link item.
- **Graph:** `CURATED` edges appear beside machine edges in a neighbourhood.
- **API:** `GET /api/curation/documents/{id}/tags` (tags with `span_count`
  and the current decision), `GET /api/curation/proposals`,
  `GET /api/curation/events`.

## Gaps

What the five questions need and rc.2 does not yet record or show:

| Question | Gap |
|---|---|
| where | Spans are stored but not shown: no highlighted occurrences in the document view, and the tags API removes them. `page` is never filled. |
| how | The dictionary entry that matched (which label, which language) is not recorded; only the confidence implies the label type. NER records its model per document, not per tag. Linking overwrites the NER tag, so the original surface text, class and score are lost. |
| how sure | Dictionary and spaCy confidences are fixed per method, not calibrated. (Fixed after rc.2: a dictionary tag now takes the best label type over all its occurrences, not that of its first one.) |
| which version | NER, pattern and curator-added tags carry no pack version. `Proposal.pack_id` and `pack_version` exist but are never filled. |
| what people decided | `Review` rows are updated in place, and the previous tag (with its spans and confidence) is not kept when a tag is rejected. (Fixed after rc.2: every decision is a new `Proposal`, the newest one wins, and a reversed decision is applied again.) |

Two defects affected the record directly; both are fixed after rc.2:

1. Fixed: reversing a decision and then reversing it back (accept, reject,
   accept) returned the first stored proposal without applying it again.
   The proposal log is now append-only: each decision is a new row with
   its own `external_ref` (the outbox event names the one it supersedes),
   the newest one is applied, and repeating the decision in force is a
   no-op.
2. Fixed: for named entities that are not pack individuals, the `CURATED`
   edge was written to a node keyed by the full IRI. It now uses the graph
   stage's key, `<Class>/<label>` or its pseudonym, so the decision sits on
   the node the machine linked.

## Proposed 1.0 record

To close the gaps without changing the storage layout:

- add `matched_label`, `label_type`, `lang`, `model` and `stage_version` to
  `Tag`, and keep `linked_from {iri, label, confidence}` when the link stage
  rewrites an NER tag;
- fill `Proposal.pack_id` and `pack_version` from the tag being decided;
- keep the rejected tag in the proposal's `params` (the log itself is
  append-only, with the latest decision winning, since the fix after rc.2);
- return spans from the tags API and highlight them in the document view,
  with a "why this tag" panel showing method, matched label, confidence,
  pack version and the decision history.
