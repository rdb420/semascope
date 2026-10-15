# Concepts

## Collection

A named corpus inside one Semascope deployment: its sources, its packs, its
pipeline profile and who may read or curate it. One Solr collection and one
Neo4j database serve every collection, with the collection name as a filter.

## Source

One configured connector instance inside a collection: a folder, a site, a
feed, a mailbox, a bucket, an RDF endpoint, or the collection's upload area.
A source runs on demand (`POST /sources/{id}/run`, `oss index`) or on a
schedule (`every 15m`, `every 2h`, or a cron expression). Each run
re-discovers the source, re-queues only new or changed items (by content
hash), and marks items the source no longer yields as deleted.

Connectors: `filesystem` (a folder under the import directory, include and
exclude globs), `web` (pages, a sitemap, or a bounded same-host crawl that
respects robots.txt and refuses private addresses), `upload` (files sent
through the API or `oss upload`), `feed` (RSS and Atom), `mail` (mbox and
eml, attachments as child documents), `archive` (zip and tar with member,
size, ratio and depth limits), `table` (CSV, TSV and XLSX rows as documents).

## Pack

A knowledge model packaged as an `ontology` skill project bundle:
`requirements.yaml`, `model.yaml`, `ontology.ttl` (OWL classes and
properties), `taxonomy.ttl` (SKOS schemes and concepts), `shapes.ttl` (SHACL),
`instances.ttl` (named individuals), `queries/` (competency questions),
evidence and decisions, and a `release-manifest.json` of content hashes.
Semascope imports, validates, versions, diffs and exports packs; it never
edits one silently. SKOS concepts and, when the pack opts in, named individuals tag text
(`semascope.tag_schemes` and `semascope.tag_individuals` in
`requirements.yaml`); OWL classes become entity types, facet families and
graph labels; shapes validate extracted metadata; competency questions run
at import against the pack's triples in the embedded store.

A pack can be **reconciled against**: `/api/reconcile` is a W3C
Reconciliation Service (0.2) over the active packs' concepts and
individuals, so OpenRefine and similar tools resolve names to pack IRIs.

Curators **propose changes** (`PackDelta`): another name for a concept, a new
concept in a scheme, a label to retire, or a note. A proposed name tags the
proposing collection at once as a draft dictionary entry; the pending deltas
export as `taxonomy.delta.ttl` and `model.delta.yaml`, terms marked `draft`
and `proposed`, for the ontology-skill workflow to accept into the next pack
version. The imported pack itself is never edited.

## Profile

An ordered list of pipeline stages for a collection, validated as a DAG of
`requires` and `provides`. `default`, `fast` and `full` ship with the
product.

## Stage

One step of the pipeline, declared with the queue it runs on and the fields
it reads and writes. Stages are plugins registered through entry points.
Stages: `fetch` (store the original under its content hash), `extract`
(Tika text and metadata, OCR for images and scanned PDFs, language
detection), `structure` (pages and chunks), `patterns` (email addresses,
links, phone numbers, amounts, hashtags), `tag` (dictionary tagging against
the active packs through Solr's Tagger, with a hierarchical facet per
scheme), `ner` (spaCy named entities), `link` (NER entities to pack
individuals by label), `validate` (SHACL over the document's metadata graph,
`full` profile), `preview` (PDF page images and image thumbnails), `index`
(Solr). Every run is recorded on the document with
its outcome and duration, and `reindex` replays everything after `extract`
from the stored text without touching Tika.

## Proposal

The record of a curation decision: accept or reject a machine tag, add a
synonym, propose a concept, merge entities, add a note. Proposals are applied
to Semascope's own stores at once and exported through the outbox so an
external system of record can mirror, reject or audit them.
