# Similar documents, exports and annotations

Capabilities that connect Semascope to other tools and help judge its
search ([ADR-0012](adr/0012-never-delivered-capabilities.md)). Every one
respects collection roles: you see, export and score only what you may
read, and importing or writing annotations needs the curator role.

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

## Analysis views

The **analysis** view of a search (a link beside table, entities and graph;
the API routes take the search as `search`, a body as for `/api/search`)
reads the search's first hits, each cut to its first 100,000 characters
(500,000 for the word tree):

- **Clusters** (`POST /api/analytics/clusters`, `k`, `sample` up to 500,
  `features`): groups of documents that use the same words. Each document
  is a TF-IDF vector over the words two or more of them share (stop words
  of their languages left out); spherical k-means with a fixed seed groups
  them, so the same documents give the same clusters. `features=vectors`
  groups by meaning instead, from two passages per document embedded with
  the search embedder (at most 100 documents; chunk vectors are indexed but
  not stored, so they are not read back). A cluster is labelled by the words
  whose weight in it most exceeds their weight elsewhere; its documents are
  listed nearest the centre first, with how alike they are (cosine, 0–1).
- **Compare** (`POST /api/analytics/compare`, `against`): the words that set
  the search's documents apart from those of a second search, or from every
  document of the collections searched. Words are counted in documents and
  ranked by log-likelihood (G²); each side lists the words more typical of it.
- **Word tree** (`POST /api/analytics/wordtree`, `phrase`, `side`, `depth`
  up to 8, `branches` up to 20): what follows (or precedes) a word or phrase,
  as a tree of shared continuations, never across a sentence end; at most
  5,000 occurrences.
- **Diff** (`GET /api/analytics/diff?a=<id>&b=<id>`, the **diff** link of a
  similar document): two documents line by line, unchanged runs collapsed,
  replaced lines marked word by word; each text is compared up to 400,000
  characters or 20,000 lines. It needs reader on both documents.

## Why a result ranked

Each search result has a **why?** link (`POST /api/explain` with `search` and
`doc_id`). For a keyword search it shows the hit's rank among the first
1,000 results, its score and the query clauses that matched it (field, word,
weight) with Solr's explanation; in semantic and hybrid modes, its rank by
its best passage and its passages nearest the query; in hybrid mode, the
two reciprocal-rank shares (1 / (60 + rank − 1) each) that add up to the
fused score. Solr's explanation carries statistics of the whole index (how
many documents hold a word, in every collection), so only staff see it whole;
everyone else sees it without those parts.

## Tables

A search's documents, or the concepts and entities on them, as CSV, JSON
Lines or Parquet:

```bash
oss export documents "flood" --collection demo --format parquet --out flood.parquet
oss export entities --collection demo --format csv --out entities.csv
# or: POST /api/export/documents?format=csv|jsonl|parquet&limit=...&text_chars=...
#     POST /api/export/entities?format=...&limit=...   with a search body
```

`documents` has one row per document: `id`, `collection`, `source_id`,
`title`, `uri`, `media_type`, `content_type_group`, `language`, `size`,
`pages`, `status`, `content_hash`, `modified_at`, `indexed_at` (UTC
timestamps in Parquet), `concepts` and `concept_iris` (the concepts in force;
lists in JSON Lines and Parquet, joined with `; ` in CSV), `entities` (how
many) and `page_url`; `text_chars` adds the start of the text (`text`, up to
1,000,000 characters). `entities` has one row per document and tag in force:
`document_id`, `collection`, `kind` (concept or entity), `class` (Person,
Place, ... for named entities), `iri` (as in the RDF export), `label`,
`confidence`, `stage`, `pack_id`, `pack_version`, `mentions` and
`linked_from`. Both follow the search's order, stream (Parquet one row group
of 500 rows at a time) and stop at `limit` documents (1,000 by default,
10,000 at most). In a collection that pseudonymises, named entities are
exported by their HMAC key and without their names. The [dashboards
recipes](dashboards.md) read these files.

## RDF exports

A document, or the hits of a search, with their tags in force and the
notes and decisions on them, as Turtle, JSON-LD or N-Quads:

```bash
oss export document <id> --format turtle --out doc.ttl
oss export search "flood" --collection demo --format jsonld --limit 5000 --out flood.jsonld
# or: GET /api/export/documents/<id>?format=turtle|jsonld|nquads
#     POST /api/export/search?format=...&limit=...  with a search body as for /api/search
```

In N-Quads each document's triples are in a named graph, the document's
`urn:semascope:doc:` IRI, so a triplestore can replace one document's
statements when it changes.

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

## A collection's graph

A lighter export for triplestores and graph tools: a collection's indexed
documents (`dcterms:title`, `base:inCollection`) linked by `dcterms:subject`
to every concept and entity they hold in force, the concepts with their
`skos:prefLabel`, `skos:inScheme` and `skos:broader`, individuals typed with
their pack class and named entities with their base class. In N-Quads the
whole export is one named graph, the collection's `urn:semascope:collection:`
IRI. Reader on the collection; at most 10,000 documents.

```bash
oss export graph --collection demo --format nquads --out demo.nq
# or: GET /api/export/graph?collection=demo&format=turtle|jsonld|nquads
```

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

### Web Annotation Protocol

`/api/annotations/w3c` is also an annotation container in the sense of the
[Web Annotation Protocol](https://www.w3.org/TR/annotation-protocol/) (its
`Link` header says `ldp#BasicContainer`):

```bash
# create: 201 with Location and ETag (curator on the document's collection)
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/ld+json" \
  --data '{"type": "Annotation", "bodyValue": "the opening",
           "target": {"source": "urn:semascope:doc:<id>",
                      "selector": {"type": "TextQuoteSelector", "exact": "Tell me, O Muse"}}}' \
  https://semascope.example/api/annotations/w3c
# read (reader), replace and delete (curator, or the note's author): If-Match is required
curl -H "If-Match: <etag>" -X DELETE https://semascope.example/api/annotations/w3c/<n>
```

The target names a document by its `urn:semascope:doc:` IRI, its page in the
UI, or its original URI (add `?collection=` when that URI is in several
collections). An annotation is a note: its `id` is its URL in the container,
its `canonical` IRI the `urn:semascope:annotation:` name the exports use.
Creating and replacing record an `add_note` decision, as notes made in the
UI do. A replace that names another document is refused (409); one sent with
an old ETag gets 412, one without `If-Match` 428.

The **export** view of a search imports annotation files too: **Check**
runs the import as a dry run and reports what it would do; **Import** then
writes it.

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
