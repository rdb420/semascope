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

## Review and proposal

The pipeline opens **review items** where a person should look: a named
entity the model found that resembles a pack individual (link it, or
reject), a tag matched on a less certain label, a SHACL violation on the
document's metadata. A decision on a review item, or directly on a
document's tags, becomes a **proposal**: the record of the decision with a
stable `external_ref`. Proposals are applied at once to Semascope's stores
(Postgres, the Solr facets, a `CURATED` edge in the graph) and written to
the **outbox** (`/api/curation/events`, read by cursor) so an external
system of record can mirror, reject or audit them. Webhooks deliver outbox
events with an HMAC signature. A reindex never undoes a decision.

## Graph

Every indexed document is projected into Neo4j: `Document`, `Source`,
`Collection`, `Concept` (pack concepts, `TAGGED`), `Entity` (pack
individuals and named entities, `MENTIONS`), `Pack`, and `CURATED` edges for
human decisions. A collection can pseudonymise named-entity keys. The UI
graph page shows the hubs of a collection and expands neighbourhoods;
`/api/graph` offers neighbours, shortest paths and a staff-only read-only
Cypher console.

## Search modes

`keyword` matches words and phrases (Solr edismax over title and text, with
highlighting). `semantic` embeds the query with the bundled FastEmbed model
and ranks documents by their best-matching chunks, returning the passages.
`hybrid` fuses both rankings with reciprocal rank fusion. Every document's
chunks are embedded by the `embed` stage into the `chunks` collection; an
OpenAI-compatible `/embeddings` endpoint can replace the bundled model
(`SEMASCOPE_EMBEDDER=openai`).

## Roles

`reader` searches and reads; `curator` decides reviews, tags and annotates;
`editor` manages collections, sources and packs; `admin` does everything and
is what Django staff and superusers are. Roles are per collection or global
(`CollectionRole` in the admin). A signed-in user without roles gets
`SEMASCOPE_DEFAULT_ROLE`. Local accounts always work; set the
`SEMASCOPE_OIDC_*` variables to add single sign-on through OpenID Connect.

## Backup and restore

`./oss backup` writes Postgres, the three Solr collections and the data
volume (originals, previews, uploads, the pack store) into
`data/backups/<stamp>`; `--with-graph` also dumps Neo4j (which otherwise
`./oss reindex` rebuilds). `./oss restore <dir>` loads a backup back. Keep
`.env` with the backup: it holds the secrets the data was written with.
