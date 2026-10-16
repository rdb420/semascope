# Similar documents, exports and annotations

Capabilities that connect Semascope to other tools and help judge its
search ([ADR-0012](adr/0012-never-delivered-capabilities.md)). Every one
respects collection roles: you see, export and score only what you may
read, and importing annotations needs the curator role.

## Similar documents

The document page lists the documents most like the open one. **Words**
uses Solr's MoreLikeThis: the document's most distinctive words (frequent
in it, present in other documents, and not in more than half of the index),
searched as one query. **Meaning** embeds four passages spread over the
document with the search embedder, averages them, and asks for the nearest
passages of other documents. **Both** (the default) fuses the two rankings;
each hit shows its rank in either list (`–` when that list missed it).

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://semascope.example/api/documents/<id>/similar?mode=hybrid&rows=10"
```

`mode` is `hybrid`, `text` or `vector`; `collection` limits the candidates
to one collection; `rows` is at most 50. A signal that cannot answer (no
embedder, no stored text) is reported in `text_error` or `vector_error`
while the other still does.

## RDF exports

A document, or the hits of a search, with their tags in force and the
notes and decisions on them, as Turtle or JSON-LD:

```bash
oss export document <id> --format turtle --out doc.ttl
oss export search "flood" --collection demo --format jsonld --limit 5000 --out flood.jsonld
# or: GET /api/export/documents/<id>?format=turtle|jsonld
#     POST /api/export/search?format=...&limit=...  with a search body as for /api/search
```

The model is the base pack's (`packs/base/ontology.ttl`):

- a `base:Document` (`urn:semascope:doc:<id>`) with `dcterms:title`,
  `identifier`, `source` (its original URI), `format`, `language` and
  `modified`, `base:contentHash`, `base:inCollection`, `base:fromSource`,
  its pages (`base:hasPage`, `base:pageNumber`) and `rdfs:seeAlso` its page
  in the UI;
- a `base:Tag` per tag in force (rejected tags are left out, accepted
  additions and links are in) with `base:tagTarget`, `base:confidence`,
  `base:producedByPack` (a `base:Pack` with `base:packVersion`) and
  `base:producedByStage` (a `base:StageRun`, which is a `prov:Activity`,
  titled with the stage); a linked entity's tag says what it was linked from
  with `prov:wasDerivedFrom`;
- concepts as `skos:Concept` with their label; named entities as
  `urn:semascope:entity:<Class>/<name>` typed with the base class;
- notes and curator decisions as `oa:Annotation`s on the document (see
  below).

The base pack's competency question CQ-001 (documents tagged with a
concept, with pack version and stage) runs unchanged on an export. A search
export follows the search's order, streams, and stops at `limit` (1,000 by
default, 10,000 at most). In a collection that pseudonymises, named
entities are exported by their HMAC key and without their names, as in the
graph.

## Web Annotations

Notes and curator decisions are available in the [W3C Web Annotation Data
Model](https://www.w3.org/TR/annotation-model/), one `AnnotationPage` of 100
at a time with `next` links:

```bash
oss export annotations --collection demo --out notes.jsonld   # follows every page
# or: GET /api/annotations/w3c?collection=demo  (or ?document=<id>; &history=true)
```

A note is a `commenting` annotation whose target is the document (its
original URI as `source`, its Semascope page as `scope`) with a position
and a quote selector when the note is about a passage; its tags are
`tagging` bodies. Decisions become annotations too: accepting or adding a
concept is `classifying` (the concept IRI and its label), an entity
`identifying`, a rejection `assessing` (the text "rejected" and the IRI),
a link `identifying` (the individual), a dismissed SHACL violation
`moderating`; the reason is a `commenting` body. Only the decision in force
per target is listed unless `history=true`.

Import W3C annotations (one, a list, an `AnnotationCollection` or
`AnnotationPage`) or a Hypothesis API export (`{"rows": [...]}`) into one
collection:

```bash
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  --data @annotations.json \
  "https://semascope.example/api/annotations/w3c/import?collection=demo&dry_run=true"
# or a multipart upload of up to 20 MB as `file`
```

Each annotation is matched to a document of that collection by its target:
the document's URI, its `urn:semascope:doc:` name, or a link to its
Semascope page. It is anchored by its quote (exact text, the occurrence
whose prefix and suffix fit best, ignoring differences in whitespace), else
by its position; one that cannot be anchored becomes a note on the whole
document. The answer counts what was imported, already imported (a
re-import skips those), unmatched, anchored and not anchored, with the
reason for the first 50 problems. Each imported note is recorded in the
decision log with the original's id, creator and time, and the export gives
them back. `dry_run=true` reports without writing.

## Browser search

The UI links an OpenSearch description, so browsers that read one (Firefox,
for instance) offer to add Semascope as a search engine. Searches open the search page,
which needs you to be signed in; suggestions come from document titles.
The description itself (`/api/opensearch.xml`) is public: it holds only
the name and two URL templates.

## Overview

The **Overview** page (and `GET /api/analytics/overview?collection=&gap=`)
shows, for the readable collections or one: the number of documents,
documents by modification date (decades, years or months), documents
indexed per day over the last 90 days, the most frequent people, places,
organisations, concepts and linked entities, types and languages (each a
link to the filtered search), and each collection's top concepts and
entities.

## Relevance evaluation

`oss eval` scores search against judged queries, per search mode:

```bash
./oss eval /app/packs/demo/judgements.yaml          # inside the stack
uv run oss eval my-judgements.yaml --collection docs --k 10 --mode keyword --mode hybrid --out report.md
```

A judgement file is YAML:

```yaml
format: semascope-judgements/1
collection: docs        # searched unless --collection says otherwise
k: 10
queries:
  - id: q01
    query: easement over the driveway
    relevant:           # a document id, its URI, or the end of its URI
      deeds/lot-12.pdf: 3   # 3 highly relevant, 2 relevant, 1 partly relevant
      deeds/lot-13.pdf: 1
```

The report gives, per mode, P@k (the share of the top k that is relevant),
R@k (the share of the relevant documents in the top k), nDCG@k (graded gain
2^grade − 1, discounted by log2 of the rank + 1, against the best possible
order) and MRR@k (1 / the rank of the first relevant hit), then nDCG per
query. Judged documents the collection does not hold are left out and
listed, so a judgement set for a whole corpus can score a subset of it.

`packs/demo/judgements.yaml` (22 queries) is generated by
`packs/demo/judge.py` from `judgement-queries.yaml`: each query names the
words that show a text is about it (in the translations' spellings), and a
text's grade follows from how often those words occur in it, by a fixed
rule; the script fails when a grade contradicts `golden.yaml`. The grades
never come from a search engine's output. See the
[recorded run](quality.md#relevance-evaluation).
