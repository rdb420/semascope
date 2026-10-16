# ADR-0012: Capabilities Open Semantic Search promised and never delivered

Status: proposed.

## Decision

Open Semantic Search documented a number of capabilities that it shipped
only as documentation, or shipped broken. Semascope delivers each such
**capability** with a modern equivalent, records the mapping here, and
does not reproduce the original technology where it is dead. The table is
the contract: a row is closed when its delivery exists with tests, and a
row marked n/a is recorded as superseded. (The review that proposed this
called it ADR "0018"; numbers from 0012 up were reassigned because 0009 to
0011 were taken.)

| Parity row | Capability | Semascope delivery | State |
|---|---|---|---|
| FUZZ-12 | More like this | `GET /api/documents/{id}/similar`: MoreLikeThis on title and text fused with nearest chunk vectors; a panel on the document page | delivered |
| ETL-EXP-04, ETL-EXP-05 | Export to RDF and a triplestore | Documents with their tags in force, notes and decisions as Turtle or JSON-LD in the base pack's vocabulary: `GET /api/export/documents/{id}`, `POST /api/export/search`, `oss export` | delivered for Turtle and JSON-LD; N-Quads and a SPARQL 1.1 Update push are open |
| ANN-07 | Other annotators | W3C Web Annotations: `GET /api/annotations/w3c` (notes and decisions), `POST /api/annotations/w3c/import` (W3C or Hypothesis exports) | export and import delivered; the protocol's per-annotation create, update and delete are open |
| SRCH-15 | Browser search integration | OpenSearch description (`/api/opensearch.xml`), suggestions, `<link rel="search">` in the UI | delivered |
| ANL-16, APP-07 | Relevance-ranking analysis | `oss eval` over judged query sets (P@k, R@k, nDCG@k, MRR) with a sample set for the demo corpus | delivered; a ranking-explain view per hit is open |
| ANL-17 | External dashboards | CSV, JSONL and Parquet exports plus a read-only Solr/SQL recipe for Grafana or Superset | open (RDF and CSV exist; the overview page covers in-product counts) |
| ETL-EXP-08, DM-20 | SQL or NoSQL export; entities in SQL | `oss export documents --format jsonl\|parquet\|sql`, `oss export entities` | open |
| ANL-12 | Clustering and topics | `/api/analytics/clusters` (k-means on chunk vectors) beside the existing result-set terms | open |
| ANL-13 | Text diff | `/api/documents/diff` and a view | open |
| ANL-14 | Corpus comparison | `/api/analytics/compare` | open |
| ANL-15 | Word tree | `/api/analytics/wordtree` and a tree view | open |
| SRCH-16 | Desktop search lenses | GNOME Shell search provider under `deploy/desktop/` | open |
| ETL-CONN-18 | File-manager "index for search" | Desktop entry and service menus calling `oss index` | open |
| FUZZ-05, FUZZ-06, FUZZ-07, APP-08, APP-09 | Name variants, lexeme and thesaurus synonyms | Wikidata-derived (CC0) given-name variants, `oss synonyms wikidata-lexemes`, `oss synonyms import` | open |
| ENR-GEO-01 | Places from a gazetteer | GeoNames (CC-BY 4.0, attributed) gazetteer stage | open |
| ENR-SPE-01 | Speech recognition | Optional faster-whisper extra and a remote provider | open |
| ETL-16, ENR-OCR-04, ENR-OCR-06, ENR-OCR-07 | Combined OCR, deskew, dual OCR, font training | OCR provider options: an ensemble, deskewing, per-collection tessdata | open |
| ETL-CONN-12, ETL-CONN-13, ETL-CONN-16, ETL-09, ETL-17, ENR-96 | Scraper, social, file-watch and sidecar connectors; task priorities; ETL integration | Owned by the ingestion and pipeline work (see the review's programme) | open |
| ENR-NER-02, DM-21, ADM-92 | Stanford NER; the legacy entities app and its configuration pin | n/a: pluggable NER providers and packs supersede them | superseded |

An analytics overview (`GET /api/analytics/overview` and an Overview page:
counts by collection, type and language, top people, places,
organisations, concepts and linked entities, documents over time) ships
with the delivered rows as the in-product half of ANL-17.

Rules every delivery here follows:

- **Read isolation.** Every route checks the collection role
  (`api/access.py`): a document needs reader on its collection, a list or
  an export is filtered to the readable collections in Solr *and* checked
  again against Postgres, an import needs curator. Every route has a rule
  in `tests/test_authz_matrix.py`.
- **Caps.** Exports stream and stop at 10,000 documents; an annotation page
  holds 100; an import takes 5,000 annotations or 20 MB; similar documents
  return at most 50.
- **Pseudonymisation travels.** A collection that pseudonymises its graph
  exports named entities by the same HMAC key and without their labels,
  in RDF and in Web Annotations alike.
- **Provenance.** Exports name Semascope's things with the `urn:semascope:`
  identifiers the validate stage already uses. An imported annotation is a
  note whose `add_note` decision records the original's id, creator and
  time; the export gives them back (`via`, `creator`, `created`) and a
  re-import skips what is already there.
- **One mount.** The new routes live in their own modules, collected by
  `api/extras.py` and mounted by one line in `api/root.py`.

## Context

The review of 1.0.0-rc.2 found 36 parity rows where Open Semantic Search
promised a capability that it shipped only as documentation or broken and
Semascope also lacked it. Leaving them out would make "everything OSS did"
an overclaim; copying the original designs (Solr2RDF, Zeppelin, desktop
lenses for Unity) would import dead technology. Recording the mapping lets
each workstream close rows independently and lets the capability matrix
cite this decision.

## Consequences

- RDF consumers get the base pack's model (CQ-001 of `packs/base` runs on
  an export) and the W3C annotation vocabulary, not a bespoke format.
- Web annotators that speak the W3C model or Hypothesis's export format can
  bring notes in; anchoring uses the quote with its context, so notes made
  on a rendered page land on the right passage of the extracted text when
  the words match.
- The OpenSearch description is served without a session: it carries only
  the product name and two URL templates, and browsers fetch it without
  the user's cookies. Searching and suggestions still need a session.
- `oss eval` turns relevance into numbers per search mode, so changes to
  analysis, synonyms or ranking can be compared on the same judgements.
  The demo judgements are derived from term counts in the corpus by a
  fixed rule (`packs/demo/judge.py`), not from any search engine's output.

## Alternatives

- A separate service for exports (a Solr-to-RDF bridge). Another process
  to run and secure, for what a streaming endpoint over Postgres does.
- Storing imported annotations' origin in new columns. Cleaner queries, but
  a migration in a module other work is changing; the decision log already
  records every note with its parameters.
- Requiring a session for the OpenSearch description. Browsers would often
  fail to add the engine, for no protection of data.
