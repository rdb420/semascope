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
| ETL-EXP-04, ETL-EXP-05 | Export to RDF and a triplestore | Documents with their tags in force, notes and decisions as Turtle, JSON-LD or N-Quads (one named graph per document) in the base pack's vocabulary: `GET /api/export/documents/{id}`, `POST /api/export/search`, `oss export`; a collection's graph (documents, concepts and entities, `dcterms:subject`, `skos:broader`): `GET /api/export/graph`, `oss export graph`; the export page in the UI | delivered; a SPARQL 1.1 Update push is open |
| ANN-07 | Other annotators | W3C Web Annotations: `GET /api/annotations/w3c` (notes and decisions; an LDP basic container), `POST /api/annotations/w3c/import` (W3C or Hypothesis exports; the export page imports files), and the Web Annotation Protocol's create (`POST /api/annotations/w3c`), read, replace and delete (`/api/annotations/w3c/{id}`, ETags and `If-Match`) | delivered |
| SRCH-15 | Browser search integration | OpenSearch description (`/api/opensearch.xml`), suggestions, `<link rel="search">` in the UI | delivered |
| ANL-16, APP-07 | Relevance-ranking analysis | `oss eval` over judged query sets (P@k, R@k, nDCG@k, MRR) with a sample set for the demo corpus; a ranking-explain view per hit (`POST /api/explain`: keyword rank, score, clauses and Solr's explanation, whole for staff only; nearest passages; fused shares) | delivered |
| ANL-17 | External dashboards | CSV, JSON Lines and Parquet exports of documents and entities (`POST /api/export/documents`, `POST /api/export/entities`) and recipes for DuckDB, Superset and Grafana (`docs/dashboards.md`); the overview page covers in-product counts | delivered |
| ETL-EXP-08, DM-20 | SQL or NoSQL export; entities in SQL | `oss export documents --format csv\|jsonl\|parquet`, `oss export entities` (one row per document and tag in force), typed columns that load into DuckDB, PostgreSQL or any SQL engine; no SQL-dump format | delivered |
| ANL-12 | Clustering and topics | `POST /api/analytics/clusters`: spherical k-means over TF-IDF term vectors, or over passage embeddings (chunk vectors are not stored, so they are not read back), labelled by significant terms; the analysis view | delivered |
| ANL-13 | Text diff | `GET /api/analytics/diff` (lines, and words within changed lines) and a view, linked from similar documents | delivered |
| ANL-14 | Corpus comparison | `POST /api/analytics/compare`: log-likelihood of document counts against another search or the index | delivered |
| ANL-15 | Word tree | `POST /api/analytics/wordtree` and a tree view | delivered |
| SRCH-16 | Desktop search lenses | GNOME Shell search provider under `deploy/desktop/` | left out (below) |
| ETL-CONN-18 | File-manager "index for search" | Desktop entry and service menus calling `oss index` | left out (below) |
| FUZZ-05, FUZZ-06, FUZZ-07, APP-08, APP-09 | Name variants, lexeme and thesaurus synonyms | Synonym tooling: what a query expands to (`GET /api/synonyms/expand`: pack labels, pending labels, Solr's analysis tokens), candidate labels for a concept (`GET /api/synonyms/suggest`: spelling variants, co-occurring names, significant words) and curators' proposals as `add_label` pack deltas, all on the Synonyms page; synonym lists come in as list packs (`import-list`). Bundled Wikidata (CC0) given-name variants and `oss synonyms wikidata-lexemes` | tooling delivered; bundled name-variant data open |
| ENR-GEO-01 | Places from a gazetteer | GeoNames (CC-BY 4.0, attributed) gazetteer stage | left out (below) |
| ENR-SPE-01 | Speech recognition | Optional faster-whisper extra and a remote provider | left out (below) |
| ETL-16, ENR-OCR-04, ENR-OCR-06, ENR-OCR-07 | Combined OCR, deskew, dual OCR, font training | OCR provider options: an ensemble, deskewing, per-collection tessdata | left out (below) |
| ETL-CONN-12, ETL-CONN-13, ETL-CONN-16, ETL-09, ETL-17, ENR-96 | Scraper, social, file-watch and sidecar connectors; task priorities; ETL integration | Owned by the ingestion and pipeline work (see the review's programme) | open |
| ENR-NER-02, DM-21, ADM-92 | Stanford NER; the legacy entities app and its configuration pin | n/a: pluggable NER providers and packs supersede them | superseded |

An analytics overview (`GET /api/analytics/overview` and an Overview page:
counts by collection, type and language, top people, places,
organisations, concepts and linked entities, documents over time) ships
with the delivered rows as the in-product half of ANL-17.

### Left out, and why

- **Desktop search lenses and file-manager menus** (SRCH-16, ETL-CONN-18).
  They run in each user's desktop session, outside the server's security
  model: a search provider would keep an API token in the session, and a
  service menu would upload files with it. They are tied to one desktop's
  extension API (GNOME Shell's changes with most releases), which the
  project cannot test in CI, and Semascope's deployment target is a headless
  server. The browser covers the need: the OpenSearch description (SRCH-15)
  adds Semascope to the browser's search bar, and `oss upload` and
  `oss watch` index files from a shell or a watched folder.
- **GeoNames gazetteer stage** (ENR-GEO-01). A useful gazetteer is the full
  GeoNames dump (gigabytes, updated daily) with disambiguation by context;
  shipping or fetching it is a data and licence decision (CC-BY 4.0
  attribution in every product that shows the places) that belongs to the
  operator. Places already come from NER and from packs, which may carry
  coordinates (the map page); a GeoNames-derived pack can be built with the
  pack tooling for the regions a deployment needs.
- **Speech recognition** (ENR-SPE-01). Transcribing audio and video needs
  a model of hundreds of megabytes to gigabytes, minutes of CPU per hour of
  audio, and media decoding (FFmpeg builds carry GPL components unless built
  with care); a remote provider would send recordings off the host, which is
  a privacy decision per collection. It is a pipeline provider with its own
  ADR, not a capability to bolt on here.
- **OCR ensembles, deskewing, dual OCR and font training** (ETL-16,
  ENR-OCR-04, ENR-OCR-06, ENR-OCR-07). Combining engines needs a second
  maintained, permissively licensed OCR engine of Tesseract's coverage, and
  a rule for merging their outputs that is measured, not guessed; Tesseract
  already corrects small skew in its layout analysis; font training is an
  offline Tesseract workflow, not a product feature. The OCR languages per
  collection (ENR-OCR-03) and OCR-later (ADR-0014) cover the configuration
  that matters most.

Rules every delivery here follows:

- **Read isolation.** Every route checks the collection role
  (`api/access.py`): a document needs reader on its collection, a list or
  an export is filtered to the readable collections in Solr *and* checked
  again against Postgres, an import needs curator. Every route has a rule
  in `tests/test_authz_matrix.py`.
- **Caps.** Exports stream and stop at 10,000 documents (an entity table at
  1,000,000 rows); an annotation page holds 100; an import takes 5,000
  annotations or 20 MB, one protocol request 1 MB; similar documents return
  at most 50. An analysis view reads at most 500 of a search's documents
  (100 by meaning, 200 for the word tree), each cut to 100,000 characters
  (500,000 for the word tree, which stops at 5,000 occurrences); a diff
  compares 400,000 characters or 20,000 lines of each text; the explain
  view ranks within the first 1,000 results.
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
- **What the index knows about other collections stays there.** Solr's
  scoring explanation reports term statistics of the whole index, every
  collection included, so the explain view gives it whole to staff only and
  strips those statistics for everyone else.

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
  analysis, synonyms or ranking can be compared on the same judgements; the
  explain view shows why one hit ranked where it did.
- Clusters, comparisons and word trees read stored text from Postgres, not
  the index, so they need no extra fields or reindexing; they are samples
  of a search, not of a whole corpus, and say how many documents they read.
- A synonym a curator proposes tags the proposing collection at once but
  expands queries only after a pack version accepts it: the synonyms file is
  shared by every collection, so one collection's draft must not change
  another's search.
- Parquet exports bring pyarrow (Apache-2.0) into the server image, and the
  clusters numpy (BSD), which the embedders already pulled in.
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
